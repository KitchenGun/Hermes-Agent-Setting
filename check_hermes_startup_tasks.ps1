param(
    [string[]]$TaskNames = @("HermesOpenCodeBridge", "HermesDiscordBot")
)

$ErrorActionPreference = "Stop"

function Get-StartupTaskStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName
    )

    $status = [ordered]@{
        TaskName       = $TaskName
        Exists         = $false
        State          = "없음"
        LastRunTime    = "N/A"
        NextRunTime    = "N/A"
        LastTaskResult = "N/A"
        Action         = "N/A"
    }

    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $taskInfo = $null

        try {
            $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
        }
        catch {
            $taskInfo = $null
        }

        $status.Exists = $true
        $status.State = [string]$task.State

        if ($taskInfo) {
            if ($taskInfo.LastRunTime -and $taskInfo.LastRunTime -ne [datetime]::MinValue) {
                $status.LastRunTime = $taskInfo.LastRunTime.ToString("yyyy-MM-dd HH:mm:ss")
            }

            if ($taskInfo.NextRunTime -and $taskInfo.NextRunTime -ne [datetime]::MinValue) {
                $status.NextRunTime = $taskInfo.NextRunTime.ToString("yyyy-MM-dd HH:mm:ss")
            }

            $status.LastTaskResult = [string]$taskInfo.LastTaskResult
        }

        if ($task.Actions -and $task.Actions.Count -gt 0) {
            $action = $task.Actions[0]
            $actionParts = @()

            if ($action.Execute) {
                $actionParts += $action.Execute
            }

            if ($action.Arguments) {
                $actionParts += $action.Arguments
            }

            $status.Action = ($actionParts -join " ").Trim()
        }
    }
    catch {
        $status.State = "조회 실패"
        $status.Action = $_.Exception.Message
    }

    [pscustomobject]$status
}

Write-Host "Hermes 시작 작업 상태"
Write-Host "----------------------"

foreach ($taskName in $TaskNames) {
    $status = Get-StartupTaskStatus -TaskName $taskName

    Write-Host "작업명: $($status.TaskName)"
    Write-Host "존재 여부: $($status.Exists)"
    Write-Host "상태: $($status.State)"
    Write-Host "마지막 실행: $($status.LastRunTime)"
    Write-Host "다음 실행: $($status.NextRunTime)"
    Write-Host "마지막 결과 코드: $($status.LastTaskResult)"
    Write-Host "작업 동작: $($status.Action)"
    Write-Host ""
}
