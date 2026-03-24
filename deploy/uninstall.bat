@echo off
:: Uninstall both NMS agents from Windows services
:: Requires: admin privileges, NSSM at deploy\tools\nssm.exe

set NSSM=%~dp0tools\nssm.exe

echo Stopping and removing NMSCoreAgent...
"%NSSM%" stop NMSCoreAgent
"%NSSM%" remove NMSCoreAgent confirm

echo Stopping and removing NMSTrackingAgent...
"%NSSM%" stop NMSTrackingAgent
"%NSSM%" remove NMSTrackingAgent confirm

echo Done. Both services removed.
echo NOTE: Install directories and config files are NOT deleted.
echo       Remove manually if needed:
echo         C:\Program Files\NMS\
echo         C:\ProgramData\nms-agent\
echo         C:\ProgramData\nms-tracking-agent\
