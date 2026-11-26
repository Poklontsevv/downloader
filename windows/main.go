package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"

	webview "github.com/jchv/go-webview2"
	"github.com/jchv/go-webview2/webviewloader"
	"golang.org/x/sys/windows"
)

func dialog(message string, flags uintptr) {
	user32 := windows.NewLazySystemDLL("user32.dll")
	messageBox := user32.NewProc("MessageBoxW")
	text, _ := windows.UTF16PtrFromString(message)
	title, _ := windows.UTF16PtrFromString("Videoport")
	messageBox.Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), flags)
}

func waitForPort(path string, exited <-chan struct{}) (int, error) {
	timer := time.NewTimer(90 * time.Second)
	defer timer.Stop()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-exited:
			return 0, errors.New("модуль загрузки завершился при запуске")
		case <-timer.C:
			return 0, errors.New("модуль загрузки не ответил")
		case <-ticker.C:
			if data, err := os.ReadFile(path); err == nil {
				if port, err := strconv.Atoi(strings.TrimSpace(string(data))); err == nil && port > 0 && port <= 65535 {
					return port, nil
				}
			}
		}
	}
}

func keepBackgroundDownloads(url string, exited <-chan struct{}) {
	client := &http.Client{Timeout: 4 * time.Second}
	request, err := http.NewRequest(http.MethodGet, url+"api/workspace", nil)
	if err != nil {
		return
	}
	request.Header.Set("X-Videoport", "1")
	notified := false
	for {
		response, err := client.Do(request)
		if err != nil {
			return
		}
		var workspace struct {
			ClipsBusy bool `json:"clips_busy"`
			Jobs      []struct {
				State string
			}
		}
		err = json.NewDecoder(response.Body).Decode(&workspace)
		response.Body.Close()
		if err != nil {
			return
		}
		active := workspace.ClipsBusy
		for _, job := range workspace.Jobs {
			if job.State == "queued" || job.State == "downloading" || job.State == "processing" ||
				job.State == "publishing" || job.State == "canceling" {
				active = true
				break
			}
		}
		if !active {
			return
		}
		if !notified {
			dialog("Загрузки продолжатся в фоне. Файлы появятся в выбранных папках.", 0x40)
			notified = true
		}
		select {
		case <-exited:
			return
		case <-time.After(4 * time.Second):
		}
	}
}

func chooseFolder(initial string) (string, error) {
	script := `Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Videoport'
$dialog.SelectedPath = $env:VIDEOPORT_PICKER_INITIAL
$dialog.ShowNewFolderButton = $true
try {
  if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Write($dialog.SelectedPath)
  }
} finally {
  $dialog.Dispose()
}`
	command := exec.Command("powershell.exe", "-NoProfile", "-STA", "-Command", script)
	command.Env = append(os.Environ(), "VIDEOPORT_PICKER_INITIAL="+initial)
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW}
	output, err := command.Output()
	if err != nil {
		return "", errors.New("не удалось открыть окно выбора папки")
	}
	return string(output), nil
}

func chooseVideo() (string, error) {
	script := `Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Videoport'
$dialog.Filter = 'Video (*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v;*.ts)|*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v;*.ts'
$dialog.CheckFileExists = $true
try {
  if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Write($dialog.FileName)
  }
} finally {
  $dialog.Dispose()
}`
	command := exec.Command("powershell.exe", "-NoProfile", "-STA", "-Command", script)
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW}
	output, err := command.Output()
	if err != nil {
		return "", errors.New("не удалось открыть окно выбора видео")
	}
	return string(output), nil
}

func createProcessJob() (windows.Handle, error) {
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return 0, err
	}
	limits := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
	limits.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	_, err = windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&limits)), uint32(unsafe.Sizeof(limits)))
	if err != nil {
		windows.CloseHandle(job)
		return 0, err
	}
	return job, nil
}

func assignProcessJob(job windows.Handle, pid int) error {
	child, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(pid))
	if err != nil {
		return err
	}
	defer windows.CloseHandle(child)
	return windows.AssignProcessToJobObject(job, child)
}

func bindRepositoryLink(window webview.WebView) error {
	if err := window.Bind("openVideoportRepository", func() error {
		return windows.ShellExecute(0, windows.StringToUTF16Ptr("open"),
			windows.StringToUTF16Ptr("https://github.com/Poklontsevv/downloader"), nil, nil, windows.SW_SHOWNORMAL)
	}); err != nil {
		return err
	}
	window.Init(`document.addEventListener("click", (event) => {
		const link = event.target.closest("a.source");
		if (link) { event.preventDefault(); window.openVideoportRepository(); }
	});`)
	return nil
}

func run() error {
	if version, err := webviewloader.GetInstalledVersion(); err != nil || version == "" {
		return errors.New("для окна программы нужен Microsoft Edge WebView2, входящий в Windows 11")
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	data, err := os.UserCacheDir()
	if err != nil {
		return err
	}
	data = filepath.Join(data, "Videoport")
	if err := os.MkdirAll(data, 0700); err != nil {
		return err
	}
	downloads, err := windows.KnownFolderPath(windows.FOLDERID_Downloads, 0)
	if err != nil {
		return err
	}
	output := filepath.Join(downloads, "Videoport")
	session, err := os.MkdirTemp(data, "session-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(session)
	ready := filepath.Join(session, "port")
	logPath := filepath.Join(data, "videoport.log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer logFile.Close()
	log.SetOutput(logFile)

	job, err := createProcessJob()
	if err != nil {
		return err
	}
	defer windows.CloseHandle(job)
	appDir := filepath.Dir(executable)
	programDir := appDir
	args := []string{"-m", "videoport.desktop", "--ready-file", ready, "--output", output, "--log", logPath}
	if _, err := os.Stat(filepath.Join(appDir, "videoport.installed")); err == nil {
		programDir = filepath.Dir(appDir)
		args = append(args, "--model-cache", filepath.Join(programDir, "models"))
	}
	args = append(args, "--projects", filepath.Join(programDir, "Projects"))
	engine := exec.Command(filepath.Join(appDir, "runtime", "pythonw.exe"), args...)
	engine.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NO_WINDOW}
	engine.Stdout, engine.Stderr = logFile, logFile
	if err := engine.Start(); err != nil {
		return fmt.Errorf("не удалось запустить модуль загрузки. Распакуйте ZIP целиком\n%w", err)
	}
	defer engine.Process.Kill()
	if err := assignProcessJob(job, engine.Process.Pid); err != nil {
		return err
	}
	exited := make(chan struct{})
	go func() {
		if err := engine.Wait(); err != nil {
			log.Print(err)
		}
		close(exited)
	}()
	port, err := waitForPort(ready, exited)
	if err != nil {
		return fmt.Errorf("%w\nЖурнал: %s", err, logPath)
	}
	url := fmt.Sprintf("http://127.0.0.1:%d/", port)

	window := webview.NewWithOptions(webview.WebViewOptions{
		DataPath:      filepath.Join(data, "WebView2"),
		AutoFocus:     true,
		WindowOptions: webview.WindowOptions{Title: "Videoport", Width: 920, Height: 750, Center: true, IconId: 1},
	})
	if window == nil {
		return errors.New("не удалось открыть окно Videoport")
	}
	defer window.Destroy()
	if err := window.Bind("openVideoportDownloads", func(folder string) error {
		if !filepath.IsAbs(folder) {
			return errors.New("папка недоступна")
		}
		if err := os.MkdirAll(folder, 0700); err != nil {
			return err
		}
		return exec.Command("explorer.exe", folder).Start()
	}); err != nil {
		return err
	}
	if err := window.Bind("chooseVideoportVideo", chooseVideo); err != nil {
		return err
	}
	if err := window.Bind("chooseVideoportFolder", chooseFolder); err != nil {
		return err
	}
	if err := window.Bind("openVideoportFile", func(path string) error {
		if !filepath.IsAbs(path) {
			return errors.New("файл недоступен")
		}
		switch strings.ToLower(filepath.Ext(path)) {
		case ".mkv", ".mp4", ".jpg", ".png", ".webp", ".avif":
			name, err := windows.UTF16PtrFromString(path)
			if err != nil {
				return err
			}
			return windows.ShellExecute(0, windows.StringToUTF16Ptr("open"), name, nil, nil, windows.SW_SHOWNORMAL)
		default:
			return errors.New("неизвестный формат файла")
		}
	}); err != nil {
		return err
	}
	if err := bindRepositoryLink(window); err != nil {
		return err
	}
	window.Navigate(url)
	window.Run()
	keepBackgroundDownloads(url, exited)
	return nil
}

func main() {
	runtime.LockOSThread()
	name := windows.StringToUTF16Ptr("Local\\Videoport.AppRunning")
	handle, err := windows.CreateMutex(nil, false, name)
	if handle != 0 {
		defer windows.CloseHandle(handle)
	}
	if errors.Is(err, windows.ERROR_ALREADY_EXISTS) {
		dialog("Videoport уже запущен или обновляется.", 0x40)
		return
	}
	if err != nil {
		dialog("Не удалось запустить Videoport.\n\n"+err.Error(), 0x10)
		return
	}
	if err := run(); err != nil {
		dialog("Не удалось открыть Videoport.\n\n"+err.Error(), 0x10)
	}
}
