# Source this file from the project root in bash or zsh. No global Git hooks.
# Disable with: unset -f git
if alias git >/dev/null 2>&1 || typeset -f git >/dev/null 2>&1; then
  echo '已有 git alias/function，未覆盖。请用 python3 scripts/git-evaluate.py push。'
else
  git() {
    if [ "${1-}" = push ]; then
      shift
      local lab_root
      lab_root=$(command git rev-parse --show-toplevel) || return
      if [ -f "$lab_root/scripts/git-evaluate.py" ]; then
        command python3 "$lab_root/scripts/git-evaluate.py" push "$@"
      else
        command git push "$@"
      fi
    else
      command git "$@"
    fi
  }
  echo '已启用 git push → 等待远程评测（当前终端）。关闭：unset -f git'
fi
