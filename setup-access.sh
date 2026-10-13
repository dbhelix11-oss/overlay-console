#!/usr/bin/env bash
# Menu of ways to reach the OverlayConsole pipes from anywhere.
# Pick one or more by number (e.g. "1 2 3"). Each option shows an example first.
# Nothing is changed until you confirm. Options 1-4 change local files; 5-6 only print instructions.
# Run as  source ./setup-access.sh  to have ~/.bashrc reloaded in your current shell afterwards;
# run as  ./setup-access.sh  and it offers to open a fresh shell that already has the changes
# (a script cannot change the shell that launched it).

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPES="$PROJECT/pipes"
BASHRC="$HOME/.bashrc"
MARK_BEGIN="# >>> overlayconsole >>>"
MARK_END="# <<< overlayconsole <<<"

# pipe names come from config.json when valid, else the defaults
NAMES=$(python3 -I - "$PROJECT/config.json" <<'PY' 2>/dev/null
import json, sys
d = ["alpha", "bravo", "charlie", "delta", "epsilon"]
try:
    n = json.load(open(sys.argv[1])).get("pipes")
    if isinstance(n, list) and n and all(isinstance(x, str) and x.isidentifier() for x in n):
        d = n
except Exception:
    pass
print(" ".join(d))
PY
)
[ -n "$NAMES" ] || NAMES="alpha bravo charlie delta epsilon"
FIRST=${NAMES%% *}

# shell builtins/keywords must not be shadowed by a function
funcnames() { for n in $NAMES; do type -t "$n" >/dev/null 2>&1 && [ "$(type -t "$n")" != "file" ] && continue; echo "$n"; done; }

show_menu() {
cat <<MENU

OverlayConsole pipes: $PIPES
Pipe names: $NAMES

 1) Short symlink  ~/oc  -> the pipes folder
        make 2>&1 | tee ~/oc/$FIRST

 2) Environment variable  \$OC  (added to ~/.bashrc)
        make > \$OC/$FIRST 2>&1

 3) One shell function per pipe name  (added to ~/.bashrc)
        make 2>&1 | $FIRST          # skips names that are already commands/builtins (e.g. echo)

 4) Symlinks to the pipes in a directory you choose  (literal  > $FIRST  works there)
        cd myproject && make > $FIRST 2>&1

 5) Remote over SSH  (prints instructions only; does not touch ~/.ssh)
        cmd | ssh you@thishost 'cat > ~/oc/$FIRST'

 6) Remote via socat listener on localhost + SSH tunnel  (prints instructions only)
        socat TCP-LISTEN:9001,fork,bind=127.0.0.1 - > ~/oc/$FIRST

 0) Quit
MENU
}

opt1() {
  if [ -e "$HOME/oc" ] && [ ! -L "$HOME/oc" ]; then echo "  ~/oc exists and is not a symlink; skipping."; return; fi
  ln -sfn "$PIPES" "$HOME/oc" && echo "  ~/oc -> $PIPES"
}

# rewrite the managed block in ~/.bashrc with the given body (idempotent)
write_block() {
  local body="$1" tmp
  tmp=$(mktemp)
  [ -f "$BASHRC" ] && awk -v b="$MARK_BEGIN" -v e="$MARK_END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$BASHRC" >"$tmp"
  printf '%s\n%s\n%s\n' "$MARK_BEGIN" "$body" "$MARK_END" >>"$tmp"
  cp "$tmp" "$BASHRC" && rm -f "$tmp"
}

BLOCK_BODY=""
add_block() { BLOCK_BODY+="$1"$'\n'; }

opt2() { add_block "export OC=\"$PIPES\""; echo "  will export OC=$PIPES"; }
opt3() {
  local list; list=$(funcnames | tr '\n' ' ')
  add_block "for _p in $list; do eval \"\$_p() { cat > '$PIPES'/\$_p; }\"; done; unset _p"
  echo "  will define functions: $list"
}
opt4() {
  local dir; read -r -p "  Directory to link the pipes into: " dir
  dir="${dir/#\~/$HOME}"
  [ -d "$dir" ] || { echo "  not a directory; skipping."; return; }
  for n in $NAMES; do
    if [ -e "$dir/$n" ] && [ ! -L "$dir/$n" ]; then echo "  $dir/$n exists (not a link); left alone."
    else ln -sfn "$PIPES/$n" "$dir/$n" && echo "  $dir/$n -> pipes/$n"; fi
  done
}
opt5() { cat <<EOT
  Remote over SSH (run on the OTHER machine; needs SSH access to this one, which you set up yourself):
    some_cmd 2>&1 | ssh you@thishost 'cat > ~/oc/$FIRST'
  Requires option 1 here (or use the full path $PIPES/$FIRST).
EOT
}
opt6() { cat <<EOT
  On THIS machine (one listener per pipe, loopback only; there is no authentication, so never bind a public address):
    socat TCP-LISTEN:9001,fork,bind=127.0.0.1 - > $PIPES/$FIRST
  On the OTHER machine, tunnel and send:
    ssh -N -L 9001:127.0.0.1:9001 you@thishost &
    some_cmd 2>&1 | nc 127.0.0.1 9001
EOT
}

[ "${BASH_SOURCE[0]}" != "$0" ] && SOURCED=1 || SOURCED=0

main() {
  show_menu
  read -r -p "Choose option numbers separated by spaces (e.g. 1 2 3): " CHOICES
  for c in $CHOICES; do case "$c" in [0-6]) ;; *) echo "Unknown option: $c"; return 1;; esac; done
  case " $CHOICES " in *" 0 "*|"  ") echo "Nothing selected."; return 0;; esac
  echo; echo "Selected: $CHOICES"
  read -r -p "Apply these now? [y/N] " ok
  [ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "Cancelled; nothing changed."; return 0; }
  echo

  for c in $CHOICES; do echo "Option $c:"; "opt$c"; echo; done
  if [ -n "$BLOCK_BODY" ]; then
    write_block "$BLOCK_BODY" && echo "Updated managed block in $BASHRC (open a new shell or: source ~/.bashrc)"
  fi
  echo "Done."
  if [ -n "$BLOCK_BODY" ]; then
    if [ "$SOURCED" = 1 ]; then
      source "$BASHRC" && echo "Reloaded ~/.bashrc in this shell."
    else
      read -r -p "Open a new shell with the changes now? (type exit to come back) [y/N] " ns
      if [ "$ns" = "y" ] || [ "$ns" = "Y" ]; then exec "${SHELL:-bash}" -i; fi
      echo "Not reloaded. Run: source ~/.bashrc   (or re-run as: source ./setup-access.sh)"
    fi
  fi
}
main
