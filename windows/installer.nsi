Target amd64-unicode
Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "4.0.0"
!endif
!ifndef PAYLOAD
  !error "Supply the unpacked Videoport folder in PAYLOAD."
!endif
!ifndef OUTPUT
  !error "Supply the installer path in OUTPUT."
!endif

!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Videoport"
!define APP_FOLDER "app-${VERSION}"
Name "Videoport"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Videoport"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

Var Guard
Var Stage
Var Previous
Var Target

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
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
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Videoport"
  !insertmacro LockApplication
FunctionEnd

Function un.onInit
  !insertmacro LockApplication
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
