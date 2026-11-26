Target amd64-unicode
Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "4.3.0"
!endif
!ifndef PAYLOAD
  !error "Supply the unpacked Videoport folder in PAYLOAD."
!endif
!ifndef OUTPUT
  !error "Supply the installer path in OUTPUT."
!endif

!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Videoport"
!define APP_FOLDER "app-${VERSION}"
!define BRAND "branding"
Name "Videoport"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Videoport"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
BrandingText "Videoport"
SetFont "Segoe UI" 9
InstallColors A93706 FFF7EF
RequestExecutionLevel user
SetCompressor /SOLID zlib
ShowInstDetails show
ShowUninstDetails show

Var Guard
Var Stage
Var Previous
Var Target
Var Registered

!define MUI_ICON "${BRAND}\videoport.ico"
!define MUI_UNICON "${BRAND}\videoport.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${BRAND}\header.bmp"
!define MUI_HEADERIMAGE_RIGHT
!define MUI_WELCOMEFINISHPAGE_BITMAP "${BRAND}\sidebar.bmp"
!define MUI_WELCOMEPAGE_TITLE "Установка Videoport"
!define MUI_WELCOMEPAGE_TEXT "Выберите папку для программы. Установщик скопирует Videoport и загрузит Qwen и движок локального анализа (около 1,15 ГБ).$\r$\n$\r$\nПри обновлении настройки, история и скачанные файлы сохраняются."
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_HEADER_TEXT "Папка установки"
!define MUI_PAGE_HEADER_SUBTEXT "Выберите папку для программы и моделей."
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE ValidateDirectory
!insertmacro MUI_PAGE_DIRECTORY
!define MUI_PAGE_HEADER_TEXT "Установка Videoport"
!define MUI_PAGE_HEADER_SUBTEXT "Копирование программы и подготовка Qwen."
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "Videoport готов к запуску"
!define MUI_FINISHPAGE_TEXT "Запустите Videoport из меню «Пуск». Модель Qwen готова к работе во вкладке «Нарезка»."
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

!macro LockApplication
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\Videoport.AppRunning") p.r0 ?e'
  Pop $1
  StrCpy $Guard $0
  ${If} $0 == 0
  ${OrIf} $1 == 183
    MessageBox MB_OK|MB_ICONEXCLAMATION "Закройте Videoport и дождитесь окончания фоновых задач. Затем повторите запуск."
    Abort
  ${EndIf}
!macroend

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "Требуется Windows x64."
    Abort
  ${EndIf}
  ReadRegStr $Registered HKCU "${UNINSTALL_KEY}" "InstallLocation"
  !insertmacro LockApplication
FunctionEnd

Function un.onInit
  ReadRegStr $Registered HKCU "${UNINSTALL_KEY}" "InstallLocation"
  ${If} $Registered != "$INSTDIR"
    MessageBox MB_OK|MB_ICONEXCLAMATION "Удалите текущую установку Videoport через параметры Windows." /SD IDOK
    Abort
  ${EndIf}
  !insertmacro LockApplication
FunctionEnd

Function ValidateDirectory
  ${GetRoot} "$INSTDIR" $0
  ${If} $INSTDIR == "$0"
  ${OrIf} $INSTDIR == "$0\"
    MessageBox MB_OK|MB_ICONEXCLAMATION "Выберите отдельную папку для Videoport." /SD IDOK
    Abort
  ${EndIf}
  ${If} $Registered != "$INSTDIR"
    ${If} ${FileExists} "$INSTDIR\Uninstall.exe"
    ${OrIf} ${FileExists} "$INSTDIR\${APP_FOLDER}\*.*"
      MessageBox MB_OK|MB_ICONEXCLAMATION "В этой папке уже есть программа или файл Uninstall.exe. Выберите другую папку." /SD IDOK
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

!macro RemoveRegisteredApp
  ${GetFileName} "$Previous" $0
  StrCpy $1 "$Previous" 4
  ${If} $0 == "$Previous"
  ${AndIf} $1 == "app-"
  ${AndIf} ${FileExists} "$INSTDIR\$Previous\Videoport.exe"
    RMDir /r "$INSTDIR\$Previous"
  ${EndIf}
!macroend

Section "Videoport"
  SetShellVarContext current
  Call ValidateDirectory
  ReadRegStr $Previous HKCU "${UNINSTALL_KEY}" "AppFolder"
  StrCpy $Target "$INSTDIR\${APP_FOLDER}"
  CreateDirectory "$INSTDIR"
  ClearErrors
  GetTempFileName $Stage "$INSTDIR"
  IfErrors failed
  Delete "$Stage"
  SetOutPath "$Stage"
  ClearErrors
  File /r "${PAYLOAD}\*.*"
  IfErrors failed
  ClearErrors
  FileOpen $2 "$Stage\videoport.installed" w
  IfErrors failed
  FileWrite $2 "${VERSION}"
  FileClose $2
  IfErrors failed

prepare_models:
  DetailPrint "Подготовка Qwen и llama.cpp в папке models"
  nsExec::ExecToLog '"$Stage\runtime\python.exe" -X utf8 -m videoport.install_models "$INSTDIR\models" "$LOCALAPPDATA\Videoport\clip-models" "$Registered\models"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Не удалось подготовить Qwen. Проверьте интернет и свободное место и повторите попытку." /SD IDCANCEL IDRETRY prepare_models
    SetOutPath "$INSTDIR"
    RMDir /r "$Stage"
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"

  ClearErrors
  IfFileExists "$Target\*.*" 0 new_target
  Rename "$Target" "$Stage-old"
  IfErrors failed
new_target:
  ClearErrors
  Rename "$Stage" "$Target"
  IfErrors rollback

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortCut "$SMPROGRAMS\Videoport.lnk" "$Target\Videoport.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "Videoport"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$Target\Videoport.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "AppFolder" "${APP_FOLDER}"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  RMDir /r "$Stage-old"
  ${If} $Previous != "${APP_FOLDER}"
  ${AndIf} $Registered == "$INSTDIR"
    !insertmacro RemoveRegisteredApp
  ${EndIf}
  Goto complete

rollback:
  IfFileExists "$Stage-old\*.*" 0 failed
  Rename "$Stage-old" "$Target"
failed:
  SetOutPath "$INSTDIR"
  ${If} $Stage != ""
    RMDir /r "$Stage"
  ${EndIf}
  MessageBox MB_OK|MB_ICONSTOP "Не удалось записать файлы Videoport. Проверьте свободное место и повторите попытку."
  Abort
complete:
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  ReadRegStr $Previous HKCU "${UNINSTALL_KEY}" "AppFolder"
  !insertmacro RemoveRegisteredApp
  Delete "$SMPROGRAMS\Videoport.lnk"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
