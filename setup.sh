#!/usr/bin/env bash
# ============================================================================
# Lum3on ComfyUI Bridge — Setup Script
# Cross-platform installer for macOS, Linux, and Windows (Git Bash/MSYS2)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# ---- Helpers ---------------------------------------------------------------

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

ask() {
    local prompt="$1" default="${2:-}"
    if [[ -n "$default" ]]; then
        read -rp "$(echo -e "${BOLD}${prompt}${NC} [${default}]: ")" answer
        echo "${answer:-$default}"
    else
        read -rp "$(echo -e "${BOLD}${prompt}${NC}: ")" answer
        echo "$answer"
    fi
}

ask_yn() {
    local prompt="$1" default="${2:-y}"
    while true; do
        local answer
        answer=$(ask "$prompt (y/n)" "$default")
        case "${answer,,}" in
            y|yes) return 0 ;;
            n|no)  return 1 ;;
            *)     warn "Please answer y or n" ;;
        esac
    done
}

detect_os() {
    case "$(uname -s)" in
        Darwin*)  echo "macos" ;;
        Linux*)   echo "linux" ;;
        MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
        *)        echo "unknown" ;;
    esac
}

detect_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 ]] && [[ "$minor" -ge 11 ]]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# ---- Platform-specific Houdini prefs path ----------------------------------

find_houdini_pref_dir() {
    local os="$1"
    local candidates=()

    case "$os" in
        macos)
            candidates=(
                "$HOME/Library/Preferences/houdini/21.0"
                "$HOME/Library/Preferences/houdini/20.5"
            )
            ;;
        linux)
            candidates=(
                "$HOME/houdini21.0"
                "$HOME/houdini20.5"
            )
            ;;
        windows)
            candidates=(
                "$USERPROFILE/Documents/houdini21.0"
                "$HOME/Documents/houdini21.0"
                "$USERPROFILE/Documents/houdini20.5"
                "$HOME/Documents/houdini20.5"
            )
            ;;
    esac

    for dir in "${candidates[@]}"; do
        if [[ -d "$dir" ]]; then
            echo "$dir"
            return 0
        fi
    done
    return 1
}

# ============================================================================
# MAIN
# ============================================================================

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD}  Lum3on ComfyUI Bridge — Setup${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""

OS=$(detect_os)
info "Detected OS: ${BOLD}${OS}${NC}"

# ---- Step 1: Python -------------------------------------------------------

echo ""
echo -e "${BOLD}--- Step 1: Python Check ---${NC}"

PYTHON_CMD=""
if PYTHON_CMD=$(detect_python); then
    ok "Found Python: $($PYTHON_CMD --version 2>&1)"
else
    err "Python 3.11+ is required but not found."
    err "Install Python from https://www.python.org/downloads/"
    exit 1
fi

# ---- Step 2: ComfyUI ------------------------------------------------------

echo ""
echo -e "${BOLD}--- Step 2: ComfyUI ---${NC}"

COMFYUI_PATH=""

if ask_yn "Is ComfyUI already installed?" "y"; then
    COMFYUI_PATH=$(ask "Enter the path to your ComfyUI installation" "")

    # Normalize path
    COMFYUI_PATH="${COMFYUI_PATH%/}"

    if [[ ! -f "$COMFYUI_PATH/main.py" ]] && [[ ! -f "$COMFYUI_PATH/comfyui" ]]; then
        err "Cannot find ComfyUI at: $COMFYUI_PATH"
        err "Expected to find main.py or comfyui executable"
        exit 1
    fi
    ok "ComfyUI found at: $COMFYUI_PATH"
else
    DEFAULT_COMFYUI="$HOME/ComfyUI"
    COMFYUI_PATH=$(ask "Where should ComfyUI be installed?" "$DEFAULT_COMFYUI")
    COMFYUI_PATH="${COMFYUI_PATH%/}"

    if [[ -d "$COMFYUI_PATH" ]]; then
        warn "Directory already exists: $COMFYUI_PATH"
        if ! ask_yn "Continue with this directory?" "y"; then
            exit 1
        fi
    else
        info "Cloning ComfyUI..."
        git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_PATH"
    fi

    info "Installing ComfyUI dependencies..."
    "$PYTHON_CMD" -m pip install -r "$COMFYUI_PATH/requirements.txt"
    ok "ComfyUI installed at: $COMFYUI_PATH"
fi

# ---- Step 3: Bridge Custom Node for ComfyUI --------------------------------

echo ""
echo -e "${BOLD}--- Step 3: ComfyUI Bridge Plugin ---${NC}"

CUSTOM_NODES_DIR="$COMFYUI_PATH/custom_nodes"
BRIDGE_LINK="$CUSTOM_NODES_DIR/houdini-comfyui-connection"

if [[ -L "$BRIDGE_LINK" ]]; then
    ok "Symlink already exists: $BRIDGE_LINK"
elif [[ -d "$BRIDGE_LINK" ]]; then
    warn "Directory exists at $BRIDGE_LINK (not a symlink)"
    warn "If this is from a previous install, remove it and re-run setup"
else
    mkdir -p "$CUSTOM_NODES_DIR"
    if [[ "$OS" == "windows" ]]; then
        # Windows: use junction or copy
        if command -v mklink &>/dev/null; then
            cmd //c "mklink /J \"$(cygpath -w "$BRIDGE_LINK")\" \"$(cygpath -w "$SCRIPT_DIR")\""
        else
            cp -r "$SCRIPT_DIR" "$BRIDGE_LINK"
            warn "Created copy instead of symlink (Windows without mklink)"
        fi
    else
        ln -s "$SCRIPT_DIR" "$BRIDGE_LINK"
    fi
    ok "Linked bridge plugin: $BRIDGE_LINK"
fi

# ---- Step 4: Scan workflows for missing custom nodes ----------------------

echo ""
echo -e "${BOLD}--- Step 4: Workflow & Custom Node Check ---${NC}"

if ask_yn "Scan workflow files for required custom nodes?" "y"; then
    DEFAULT_WF_DIR="$COMFYUI_PATH/user/default/workflows"
    WF_DIR=$(ask "Path to workflows directory" "$DEFAULT_WF_DIR")

    if [[ ! -d "$WF_DIR" ]]; then
        warn "Directory not found: $WF_DIR"
        warn "Skipping workflow scan"
    else
        WF_FILES=($(find "$WF_DIR" -maxdepth 2 -name "*.json" 2>/dev/null))

        if [[ ${#WF_FILES[@]} -eq 0 ]]; then
            info "No workflow JSON files found in $WF_DIR"
        else
            info "Found ${#WF_FILES[@]} workflow(s). Scanning for custom node types..."

            # Extract all node types from workflows
            ALL_TYPES=$("$PYTHON_CMD" -c "
import json, sys, os

skip_types = {'Reroute', 'PrimitiveNode', 'Note', 'MarkdownNote'}
types = set()

for path in sys.argv[1:]:
    try:
        with open(path) as f:
            data = json.load(f)
        if 'nodes' in data and isinstance(data['nodes'], list):
            for n in data['nodes']:
                t = n.get('type', '')
                if t and t not in skip_types:
                    types.add(t)
        else:
            for v in data.values():
                if isinstance(v, dict) and 'class_type' in v:
                    types.add(v['class_type'])
    except Exception as e:
        print(f'WARN: {os.path.basename(path)}: {e}', file=sys.stderr)

for t in sorted(types):
    print(t)
" "${WF_FILES[@]}" 2>/dev/null)

            # Check which are built-in ComfyUI types
            COMFYUI_BUILTINS=$("$PYTHON_CMD" -c "
import json, sys, os

nodes_dir = os.path.join(sys.argv[1], 'comfy_extras')
nodes_main = os.path.join(sys.argv[1], 'nodes.py')
# We can't easily parse Python for class names, so we check at runtime
# For now just print a marker that we need a running server
print('NEEDS_SERVER_CHECK')
" "$COMFYUI_PATH" 2>/dev/null)

            if [[ "$COMFYUI_BUILTINS" == "NEEDS_SERVER_CHECK" ]]; then
                echo ""
                info "To check which custom nodes are missing, ComfyUI needs to be running."
                info "Required node types found in your workflows:"
                echo ""
                echo "$ALL_TYPES" | while read -r t; do
                    echo "    $t"
                done
                echo ""

                if ask_yn "Is ComfyUI currently running? (we'll check /object_info)" "n"; then
                    COMFY_URL=$(ask "ComfyUI server URL" "http://127.0.0.1:8188")

                    MISSING=$("$PYTHON_CMD" -c "
import json, sys, urllib.request

workflow_types = set(sys.argv[2:])
try:
    resp = urllib.request.urlopen(sys.argv[1] + '/object_info', timeout=10)
    installed = set(json.loads(resp.read()).keys())
    missing = sorted(workflow_types - installed)
    if missing:
        for m in missing:
            print(m)
    else:
        print('ALL_INSTALLED')
except Exception as e:
    print(f'CONNECTION_ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" "$COMFY_URL" $ALL_TYPES 2>/dev/null) || true

                    if [[ "$MISSING" == "ALL_INSTALLED" ]]; then
                        ok "All required custom nodes are installed!"
                    elif [[ -n "$MISSING" ]]; then
                        echo ""
                        warn "Missing custom nodes:"
                        echo "$MISSING" | while read -r m; do
                            echo -e "    ${RED}✗${NC} $m"
                        done
                        echo ""
                        info "Install missing nodes via ComfyUI Manager or manually:"
                        info "  1. Open ComfyUI web interface"
                        info "  2. Click 'Manager' → 'Install Missing Custom Nodes'"
                        info "  3. Or install manually into: $CUSTOM_NODES_DIR/"
                    else
                        warn "Could not connect to ComfyUI server"
                        info "Start ComfyUI and re-run this step later"
                    fi
                else
                    info "No problem — you can install missing nodes later via ComfyUI Manager"
                fi
            fi
        fi
    fi
else
    info "Skipping workflow scan"
fi

# ---- Step 5: Houdini Package -----------------------------------------------

echo ""
echo -e "${BOLD}--- Step 5: Houdini Package ---${NC}"

HOUDINI_PREF=""
if HOUDINI_PREF=$(find_houdini_pref_dir "$OS"); then
    ok "Found Houdini preferences: $HOUDINI_PREF"
else
    warn "Could not auto-detect Houdini preferences directory"
    HOUDINI_PREF=$(ask "Enter your Houdini user preferences path" "")
fi

if [[ -z "$HOUDINI_PREF" ]]; then
    warn "Skipping Houdini package setup"
else
    PACKAGES_DIR="$HOUDINI_PREF/packages"
    mkdir -p "$PACKAGES_DIR"

    PACKAGE_FILE="$PACKAGES_DIR/lum3on-comfyui-bridge.json"

    # Convert to platform-appropriate paths
    BRIDGE_HOUDINI_PATH="$SCRIPT_DIR/houdini"

    cat > "$PACKAGE_FILE" <<PKGJSON
{
    "env": [
        {
            "HOUDINI_PATH": {
                "value": "${BRIDGE_HOUDINI_PATH}",
                "method": "append"
            }
        },
        { "COMFYUI_PATH": "${COMFYUI_PATH}" }
    ],
    "path": "${BRIDGE_HOUDINI_PATH}"
}
PKGJSON

    ok "Created Houdini package: $PACKAGE_FILE"
fi

# ---- Step 6: Summary -------------------------------------------------------

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${GREEN}${BOLD}  Setup Complete!${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""
echo -e "  ComfyUI:          ${BOLD}${COMFYUI_PATH}${NC}"
echo -e "  Bridge Plugin:    ${BOLD}${BRIDGE_LINK}${NC}"
echo -e "  Houdini Package:  ${BOLD}${PACKAGE_FILE:-skipped}${NC}"
echo ""
echo -e "${BOLD}Next steps:${NC}"
echo "  1. Start ComfyUI"
echo "  2. Open Houdini"
echo "  3. In /out context, create a 'Lum3on ComfyUI Render' ROP"
echo "  4. Load a workflow and hit Render"
echo ""
