# Test-Eagle.ps1
# Test script for Rainforest EAGLE-3 LOCAL API
# Uses the EAGLE-200/EAGLE-3 Local API format (device_list, device_query)
# NOT the old REST API (get_instantaneous_demand)
#
# Usage: .\Test-Eagle.ps1 -CloudId "YOUR_CLOUD_ID" -InstallCode "YOUR_INSTALL_CODE"

param(
    [Parameter(Mandatory=$true)]
    [string]$CloudId,

    [Parameter(Mandatory=$true)]
    [string]$InstallCode,

    [string]$EagleIP = "192.168.20.192"
)

$baseUrl = "https://${EagleIP}/cgi-bin/post_manager"

# Build basic auth header
$pair = "${CloudId}:${InstallCode}"
$bytes = [System.Text.Encoding]::ASCII.GetBytes($pair)
$base64 = [Convert]::ToBase64String($bytes)
$headers = @{ "Authorization" = "Basic $base64" }

function Send-EagleCommand {
    param([string]$Label, [string]$Body)
    Write-Host ""
    Write-Host "--- $Label ---" -ForegroundColor Yellow
    Write-Host "Request:" -ForegroundColor DarkGray
    Write-Host $Body -ForegroundColor DarkGray
    try {
        $response = Invoke-WebRequest -Uri $baseUrl -Method POST -Headers $headers `
            -Body $Body -ContentType "text/xml" -SkipCertificateCheck
        Write-Host "Status: $($response.StatusCode)" -ForegroundColor Green
        Write-Host "Response:" -ForegroundColor Cyan
        Write-Host $response.Content
    } catch {
        Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== EAGLE-3 Local API Test ===" -ForegroundColor Cyan
Write-Host "Target: $baseUrl"

# ---- Step 1: device_list ----
# This discovers all devices (meter, plugs, etc) and returns HardwareAddress
$cmdDeviceList = @"
<Command>
  <Name>device_list</Name>
</Command>
"@
Send-EagleCommand "Step 1: device_list (discover meter)" $cmdDeviceList

Write-Host ""
Write-Host "=== Next Steps ===" -ForegroundColor Magenta
Write-Host "Copy the HardwareAddress from the device_list response above."
Write-Host "Then run the device_query test below with that address."
Write-Host ""

# Prompt for meter address
$meterAddr = Read-Host "Enter the meter HardwareAddress (e.g. 0x00178d0000abcdef), or press Enter to skip"

if ($meterAddr -and $meterAddr.Length -gt 0) {
    # ---- Step 2: device_query for all meter variables ----
    $cmdDeviceQuery = @"
<Command>
  <Name>device_query</Name>
  <DeviceDetails>
    <HardwareAddress>$meterAddr</HardwareAddress>
  </DeviceDetails>
  <Components>
    <Component>
      <Name>Main</Name>
      <Variables>
        <Variable><Name>zigbee:InstantaneousDemand</Name></Variable>
        <Variable><Name>zigbee:CurrentSummationDelivered</Name></Variable>
        <Variable><Name>zigbee:CurrentSummationReceived</Name></Variable>
        <Variable><Name>zigbee:Multiplier</Name></Variable>
        <Variable><Name>zigbee:Divisor</Name></Variable>
      </Variables>
    </Component>
  </Components>
</Command>
"@
    Send-EagleCommand "Step 2: device_query (meter data)" $cmdDeviceQuery

    # ---- Step 3: device_details for full variable list ----
    $cmdDeviceDetails = @"
<Command>
  <Name>device_details</Name>
  <DeviceDetails>
    <HardwareAddress>$meterAddr</HardwareAddress>
  </DeviceDetails>
</Command>
"@
    Send-EagleCommand "Step 3: device_details (all available variables)" $cmdDeviceDetails
} else {
    Write-Host "Skipped device_query and device_details tests." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
