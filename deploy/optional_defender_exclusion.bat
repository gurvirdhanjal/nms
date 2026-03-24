@echo off
:: Add Windows Defender exclusions for NMS agents
:: Run this ONLY if the agent is blocked by Windows Defender after installation.
:: Requires: administrator privileges

echo Adding Windows Defender exclusions for NMS agents (requires admin)...

powershell -Command "Add-MpPreference -ExclusionPath 'C:\Program Files\NMS\NMSCoreAgent'"
powershell -Command "Add-MpPreference -ExclusionProcess 'NMSCoreAgent.exe'"
powershell -Command "Add-MpPreference -ExclusionPath 'C:\Program Files\NMS\NMSTrackingAgent'"
powershell -Command "Add-MpPreference -ExclusionProcess 'NMSTrackingAgent.exe'"

echo Done. Exclusions added.
echo NOTE: The tracking agent (NMSTrackingAgent) uses keyboard and screen capture APIs
echo       which are required for workstation monitoring. These exclusions are expected
echo       for internal on-premise deployments.
