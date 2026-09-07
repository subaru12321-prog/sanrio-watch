@echo off
rem Windowsタスクスケジューラから毎日呼ばれるラッパー。
rem 実行結果は run.log に残す（毎回上書き。恒久的な記録は state/*.json 側にある）。
rem
rem 作業ディレクトリに依存しないよう、スクリプトもログも %~dp0 起点の絶対パスで指定する
rem （タスクスケジューラ経由だと cwd が C:\Windows\System32 になることがあるため）。
cd /d "%~dp0"
python "%~dp0run_check.py" --weekly --notify > "%~dp0run.log" 2>&1
