$TaskName = "CodmonSlackNotifier"
$WorkDir = "D:\tkeita\tkeita\codomon"
# pythonw.exe を使うと黒い画面が出ずにバックグラウンドで実行されます
$PythonPath = "$WorkDir\.venv\Scripts\pythonw.exe"
$ScriptPath = "$WorkDir\main.py"

# アクション: Pythonスクリプトを実行
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument $ScriptPath -WorkingDirectory $WorkDir

# トリガー設定用のヘルパー関数
function New-DailyRepetitionTrigger {
    param($At, $IntervalMinutes, $DurationHours)
    
    # 繰り返し設定を持つダミーのトリガーを作成 (Onceなら確実に作れる)
    $Dummy = New-ScheduledTaskTrigger -Once -At "00:00" -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Hours $DurationHours)
    
    # 本番用の毎日実行トリガーを作成
    $T = New-ScheduledTaskTrigger -Daily -At $At
    
    # ダミーから繰り返し設定をコピー
    $T.Repetition = $Dummy.Repetition
    return $T
}

# 1. 朝の集中タイム: 7:00 - 10:00 (5分間隔)
$Trigger1 = New-DailyRepetitionTrigger -At "07:00" -IntervalMinutes 5 -DurationHours 3

# 2. 日中の通常タイム: 10:00 - 16:00 (30分間隔)
$Trigger2 = New-DailyRepetitionTrigger -At "10:00" -IntervalMinutes 30 -DurationHours 6

# 3. 夕方の集中タイム: 16:00 - 19:00 (5分間隔)
$Trigger3 = New-DailyRepetitionTrigger -At "16:00" -IntervalMinutes 5 -DurationHours 3

# 設定: バッテリー駆動でも実行する、すぐに開始する
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# ルートフォルダに同名のタスクがある場合は削除して整理します（場所を移動するため）
$OldTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -eq "\" }
if ($OldTask) {
    Unregister-ScheduledTask -InputObject $OldTask -Confirm:$false
    Write-Host "ルートフォルダにあった古いタスクを削除しました。"
}

# タスクの登録 (Myフォルダに作成します)
Register-ScheduledTask -Action $Action -Trigger @($Trigger1, $Trigger2, $Trigger3) -Settings $Settings -TaskName $TaskName -TaskPath "\My\" -Description "Codmonの通知を定期的にチェックしてSlackに転送します" -Force

Write-Host "タスク '$TaskName' を '\My\' フォルダに登録しました。"
Write-Host "以下のスケジュールで実行されます："
Write-Host "  - 朝: 07:00 - 10:00 (5分間隔)"
Write-Host "  - 昼: 10:00 - 16:00 (30分間隔)"
Write-Host "  - 夕: 16:00 - 19:00 (5分間隔)"
Write-Host "ログは $WorkDir\app.log で確認できます。"