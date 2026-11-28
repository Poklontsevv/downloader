package main

import (
	"context"
	"errors"
	"fmt"
	"time"
	"unsafe"

	webview "github.com/jchv/go-webview2"
	"golang.org/x/sys/windows"
)

const (
	windowTitle      = "Videoport"
	applicationMutex = "Local\\Videoport.AppRunning"
	wmClose          = 0x0010
	wmLeftButtonUp   = 0x0202
	wmTrayIcon       = 0x8001
	swHide           = 0
	swShow           = 5
	swRestore        = 9
	smtoBlock        = 0x0001
	smtoAbortIfHung  = 0x0002
	nimAdd           = 0
	nimDelete        = 2
	nifMessage       = 0x01
	nifIcon          = 0x02
	nifTip           = 0x04
)

var (
	user32                   = windows.NewLazySystemDLL("user32.dll")
	registerWindowMessage    = user32.NewProc("RegisterWindowMessageW")
	findWindow               = user32.NewProc("FindWindowW")
	getWindowThreadProcessID = user32.NewProc("GetWindowThreadProcessId")
	allowSetForegroundWindow = user32.NewProc("AllowSetForegroundWindow")
	setForegroundWindow      = user32.NewProc("SetForegroundWindow")
	sendMessageTimeout       = user32.NewProc("SendMessageTimeoutW")
	showWindow               = user32.NewProc("ShowWindow")
	isIconic                 = user32.NewProc("IsIconic")
	destroyWindow            = user32.NewProc("DestroyWindow")
	loadIcon                 = user32.NewProc("LoadIconW")
	comctl32                 = windows.NewLazySystemDLL("comctl32.dll")
	setWindowSubclass        = comctl32.NewProc("SetWindowSubclass")
	removeWindowSubclass     = comctl32.NewProc("RemoveWindowSubclass")
	defSubclassProc          = comctl32.NewProc("DefSubclassProc")
	notifyIcon               = windows.NewLazySystemDLL("shell32.dll").NewProc("Shell_NotifyIconW")
)

func registerActivationMessage() (uintptr, error) {
	message, _, err := registerWindowMessage.Call(uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("Videoport.ShowWindow"))))
	if message == 0 {
		return 0, err
	}
	return message, nil
}

func showExistingWindow(message uintptr) bool {
	// go-webview2 uses this class for its top-level window, even while it is hidden.
	hwnd, _, _ := findWindow.Call(uintptr(unsafe.Pointer(windows.StringToUTF16Ptr("webview"))),
		uintptr(unsafe.Pointer(windows.StringToUTF16Ptr(windowTitle))))
	if hwnd == 0 {
		return false
	}
	var pid uint32
	getWindowThreadProcessID.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
	if pid != 0 {
		allowSetForegroundWindow.Call(uintptr(pid))
	}
	var activated uintptr
	sent, _, _ := sendMessageTimeout.Call(hwnd, message, 0, 0, smtoBlock|smtoAbortIfHung, 250,
		uintptr(unsafe.Pointer(&activated)))
	return sent != 0 && activated == 1
}

func acquireApplication(activationMessage uintptr) (windows.Handle, error) {
	name := windows.StringToUTF16Ptr(applicationMutex)
	deadline := time.Now().Add(2500 * time.Millisecond)
	for {
		handle, err := windows.CreateMutex(nil, false, name)
		if err == nil {
			return handle, nil
		}
		if handle != 0 {
			windows.CloseHandle(handle)
		}
		if !errors.Is(err, windows.ERROR_ALREADY_EXISTS) {
			return 0, err
		}
		if showExistingWindow(activationMessage) {
			return 0, nil
		}
		if time.Now().After(deadline) {
			dialog("Videoport запускается, уже запущен или обновляется. Повторите попытку через несколько секунд.", 0x40)
			return 0, nil
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// notifyIconData matches NOTIFYICONDATAW on Windows.
type notifyIconData struct {
	size             uint32
	hwnd             uintptr
	id               uint32
	flags            uint32
	callbackMessage  uint32
	icon             uintptr
	tooltip          [128]uint16
	state            uint32
	stateMask        uint32
	info             [256]uint16
	timeoutOrVersion uint32
	infoTitle        [64]uint16
	infoFlags        uint32
	guid             windows.GUID
	balloonIcon      uintptr
}

type desktopWindow struct {
	view             webview.WebView
	hwnd             uintptr
	url              string
	engineExited     <-chan struct{}
	subclass         uintptr
	trayAdded        bool
	cancelBackground context.CancelFunc
	closing          bool
}

func attachDesktopWindow(view webview.WebView, url string, exited <-chan struct{}, activationMessage uintptr) (*desktopWindow, error) {
	window := &desktopWindow{view: view, hwnd: uintptr(view.Window()), url: url, engineExited: exited}
	window.subclass = windows.NewCallback(func(hwnd, message, wParam, lParam, _, _ uintptr) uintptr {
		switch message {
		case wmClose:
			window.hide()
			return 0
		case activationMessage:
			if window.closing {
				return 0
			}
			window.show()
			return 1
		case wmTrayIcon:
			if lParam == wmLeftButtonUp && !window.closing {
				window.show()
			}
			return 0
		}
		result, _, _ := defSubclassProc.Call(hwnd, message, wParam, lParam)
		return result
	})
	if ok, _, err := setWindowSubclass.Call(window.hwnd, window.subclass, 1, 0); ok == 0 {
		return nil, fmt.Errorf("не удалось настроить повторное открытие окна: %w", err)
	}
	return window, nil
}

func (window *desktopWindow) hide() {
	if window.closing || window.cancelBackground != nil {
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	window.cancelBackground = cancel
	window.addTrayIcon()
	showWindow.Call(window.hwnd, swHide)
	go func() {
		waitForBackgroundTasks(ctx, window.url, window.engineExited, true, nil)
		if ctx.Err() == nil {
			window.view.Dispatch(func() {
				// A reopened window must survive a status check from an earlier close.
				if ctx.Err() == nil {
					window.quit()
				}
			})
		}
	}()
}

func (window *desktopWindow) show() {
	window.stopBackground()
	mode := uintptr(swShow)
	if minimized, _, _ := isIconic.Call(window.hwnd); minimized != 0 {
		mode = swRestore
	}
	showWindow.Call(window.hwnd, mode)
	setForegroundWindow.Call(window.hwnd)
}

func (window *desktopWindow) quit() {
	window.closing = true
	window.view.Terminate()
}

func (window *desktopWindow) addTrayIcon() {
	var module windows.Handle
	if err := windows.GetModuleHandleEx(windows.GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, nil, &module); err != nil {
		return
	}
	icon, _, _ := loadIcon.Call(uintptr(module), 1)
	if icon == 0 {
		return
	}
	data := notifyIconData{hwnd: window.hwnd, id: 1, flags: nifMessage | nifIcon | nifTip, callbackMessage: wmTrayIcon, icon: icon}
	data.size = uint32(unsafe.Sizeof(data))
	copy(data.tooltip[:], windows.StringToUTF16("Videoport — открыть окно"))
	ok, _, _ := notifyIcon.Call(nimAdd, uintptr(unsafe.Pointer(&data)))
	window.trayAdded = ok != 0
}

func (window *desktopWindow) stopBackground() {
	if window.cancelBackground != nil {
		window.cancelBackground()
		window.cancelBackground = nil
	}
	if window.trayAdded {
		data := notifyIconData{hwnd: window.hwnd, id: 1}
		data.size = uint32(unsafe.Sizeof(data))
		notifyIcon.Call(nimDelete, uintptr(unsafe.Pointer(&data)))
		window.trayAdded = false
	}
}

func (window *desktopWindow) detach() {
	window.stopBackground()
	if window.subclass != 0 {
		removeWindowSubclass.Call(window.hwnd, window.subclass, 1)
		window.subclass = 0
	}
}
