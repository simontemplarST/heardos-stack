# @id: script_1792800000001_toolsrun1
# @title: Maintenance console - run a script with a log
# @description: Used by the Maintenance app (OliveTin): runs a script, shows its output live and keeps a copy in CronMaster's log folder data/logs/manual-<name>/ in the same format as scheduled runs
#!/usr/bin/env bash
# tools-run.sh NAME COMMAND [ARGS...] — run COMMAND, echo its output (OliveTin shows it live) and append the same text,
# framed like the CronMaster log wrapper, to data/logs/manual-NAME/<timestamp>.log. Exit code = the command's.
set -u
name="$1"; shift
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../data" && pwd)/logs/manual-$name"
mkdir -p "$dir"
log="$dir/$(date '+%Y-%m-%d_%H-%M-%S').log"
start=$SECONDS
{
  echo "--- [ JOB START ] ----------------------------------------------------"
  echo "Command   : $*"
  echo "Timestamp : $(date '+%Y-%m-%d %H:%M:%S')"
  echo "Host      : $(hostname)"
  echo "User      : $(whoami) (manual run from the desktop)"
  echo "--- [ JOB OUTPUT ] ---------------------------------------------------"
  echo
} | tee "$log"
"$@" 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}
{
  echo
  echo "--- [ JOB SUMMARY ] --------------------------------------------------"
  echo "Timestamp : $(date '+%Y-%m-%d %H:%M:%S')"
  echo "Duration  : $((SECONDS - start))s"
  echo "Exit Code : $rc"
  echo "Status    : $([ "$rc" -eq 0 ] && echo SUCCESS || echo FAILED)"
  echo "--- [ JOB END ] ------------------------------------------------------"
} | tee -a "$log"
exit "$rc"
