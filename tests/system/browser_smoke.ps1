param(
    [string]$Url = 'http://localhost:8766/',
    [string]$TokenFile = '',
    [switch]$TokenFromStdin,
    [string]$ExpectedRunId = '',
    [string]$ExpectedSourceSha256 = ''
)
$ErrorActionPreference = 'Stop'
$tab = Invoke-RestMethod -Method Put -Uri 'http://localhost:9223/json/new?about:blank'
$socket = [System.Net.WebSockets.ClientWebSocket]::new()
[void]$socket.ConnectAsync([Uri]$tab.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
$script:exceptions = 0
$script:failedRequests = 0

function Send-Cdp($id, $method, $parameters) {
    $message = @{ id = $id; method = $method; params = $parameters } | ConvertTo-Json -Compress -Depth 10
    $bytes = [Text.Encoding]::UTF8.GetBytes($message)
    $segment = [ArraySegment[byte]]::new($bytes)
    [void]$socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
}

function Read-Until($wantedId) {
    while ($true) {
        $stream = [IO.MemoryStream]::new()
        do {
            $bytes = New-Object byte[] 65536
            $segment = [ArraySegment[byte]]::new($bytes)
            $timeout = [Threading.CancellationTokenSource]::new(10000)
            try {
                $received = $socket.ReceiveAsync($segment, $timeout.Token).GetAwaiter().GetResult()
            } finally {
                $timeout.Dispose()
            }
            if ($received.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
                throw 'Chrome closed the debugger connection'
            }
            $stream.Write($bytes, 0, $received.Count)
        } while (-not $received.EndOfMessage)
        $event = [Text.Encoding]::UTF8.GetString($stream.ToArray()) | ConvertFrom-Json
        if ($event.method -eq 'Runtime.exceptionThrown') { $script:exceptions += 1 }
        if ($event.method -eq 'Network.loadingFailed') { $script:failedRequests += 1 }
        if ($event.id -eq $wantedId) { return $event }
    }
}

try {
    Send-Cdp 1 'Runtime.enable' @{}
    [void](Read-Until 1)
    Send-Cdp 2 'Network.enable' @{}
    [void](Read-Until 2)
    Send-Cdp 3 'Page.enable' @{}
    [void](Read-Until 3)
    Send-Cdp 4 'Page.navigate' @{ url = $Url }
    [void](Read-Until 4)
    Start-Sleep -Milliseconds 1200
    Send-Cdp 5 'Runtime.evaluate' @{
        expression = '({title:document.title,hasCommandCenter:document.body.innerText.includes("ORION"),source:document.getElementById("source").textContent,tokenField:!!document.getElementById("reportToken")})'
        returnByValue = $true
    }
    $page = Read-Until 5
    Send-Cdp 6 'Runtime.evaluate' @{
        expression = 'document.getElementById("connectReport").click(); document.getElementById("connectionStatus").textContent'
        returnByValue = $true
    }
    $emptyToken = Read-Until 6
    $authenticated = $null
    $denied = $null
    $cleared = $null
    if ($TokenFile -or $TokenFromStdin) {
        $token = if ($TokenFromStdin) { [Console]::ReadLine() } else { [IO.File]::ReadAllText($TokenFile).Trim() }
        $parts = $token.Split('.')
        $replacement = if ($parts[2][0] -eq 'A') { 'B' } else { 'A' }
        $parts[2] = $replacement + $parts[2].Substring(1)
        $forgedJson = ConvertTo-Json -InputObject ($parts -join '.') -Compress
        Send-Cdp 7 'Runtime.evaluate' @{
            expression = "document.getElementById('reportToken').value=$forgedJson; document.getElementById('connectReport').click();"
            returnByValue = $true
        }
        [void](Read-Until 7)
        Start-Sleep -Milliseconds 1500
        Send-Cdp 8 'Runtime.evaluate' @{
            expression = '({status:document.getElementById("connectionStatus").textContent,observations:document.getElementById("observations").textContent})'
            returnByValue = $true
        }
        $denied = (Read-Until 8).result.result.value
        $tokenJson = ConvertTo-Json -InputObject $token -Compress
        Send-Cdp 9 'Runtime.evaluate' @{
            expression = "document.getElementById('reportToken').value=$tokenJson; document.getElementById('connectReport').click();"
            returnByValue = $true
        }
        [void](Read-Until 9)
        Start-Sleep -Milliseconds 1800
        Send-Cdp 10 'Runtime.evaluate' @{
            expression = '({status:document.getElementById("connectionStatus").textContent,metadata:document.getElementById("metadata").textContent,observations:document.getElementById("observations").textContent,source:document.getElementById("source").textContent,history:document.getElementById("historySummary").textContent})'
            returnByValue = $true
        }
        $authenticated = (Read-Until 10).result.result.value
        if ($ExpectedRunId -and -not $authenticated.history.Contains($ExpectedRunId)) { throw 'Run identity missing from browser report' }
        if ($ExpectedSourceSha256 -and -not $authenticated.history.Contains($ExpectedSourceSha256)) { throw 'Source digest missing from browser report' }
        Send-Cdp 12 'Runtime.evaluate' @{
            expression = "document.getElementById('reportToken').value=$forgedJson; document.getElementById('connectReport').click();"
            returnByValue = $true
        }
        [void](Read-Until 12)
        Start-Sleep -Milliseconds 1500
        Send-Cdp 13 'Runtime.evaluate' @{
            expression = '({status:document.getElementById("connectionStatus").textContent,observations:document.getElementById("observations").textContent,history:document.getElementById("historySummary").textContent})'
            returnByValue = $true
        }
        $cleared = (Read-Until 13).result.result.value
        $token = $null
    }
    Send-Cdp 11 'Runtime.evaluate' @{ expression = 'document.readyState'; returnByValue = $true }
    [void](Read-Until 11)
    [pscustomobject]@{
        title = $page.result.result.value.title
        has_command_center = $page.result.result.value.hasCommandCenter
        source = $page.result.result.value.source
        token_field = $page.result.result.value.tokenField
        empty_token_state = $emptyToken.result.result.value
        denied = $denied
        authenticated = $authenticated
        cleared_after_denial = $cleared
        exceptions = $script:exceptions
        failed_requests = $script:failedRequests
    } | ConvertTo-Json -Compress
} finally {
    $socket.Dispose()
}
