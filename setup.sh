#!/bin/bash
# Space Lab setup and repair. Everything installs inside this folder (.tools, .venv, .venv-exotic).
# Start Meridian.command runs this by itself when Space Lab's environment is missing or broken.
set -e
cd "$(dirname "$0")"
HERE="$(pwd)"
TOOLS="$HERE/.tools"
mkdir -p "$TOOLS" logs data inbox/exoplanet-watch
export UV_INSTALL_DIR="$TOOLS/bin" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1
export UV_CACHE_DIR="$TOOLS/cache" UV_PYTHON_INSTALL_DIR="$TOOLS/python"

find_uv() { find "$TOOLS" -name uv -type f -perm +111 2>/dev/null | head -1; }
UV="$(find_uv)"
if [ -z "$UV" ]; then
  echo "[1/4] Installing uv (fast Python installer) into SpaceLab/.tools ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  UV="$(find_uv)"
fi
if [ -z "$UV" ]; then echo "Could not install uv. Paste this window's text to Claude."; exit 1; fi
echo "[1/4] uv ready: $("$UV" --version)"

# Recreate an environment if it is missing or broken (for example after the folder was moved).
make_env() { "$1/bin/python" -c "import sys" >/dev/null 2>&1 || { rm -rf "$1"; "$UV" venv --python 3.12 "$1" >/dev/null; }; }

echo "[2/4] Main environment: Python 3.12 + astronomy packages (first time ~3 min) ..."
make_env "$HERE/.venv"
"$UV" pip install --quiet --python "$HERE/.venv/bin/python" -r requirements.txt
echo "      ok"

echo "[3/4] EXOTIC environment (NASA Exoplanet Watch software, first time ~5 min) ..."
make_env "$HERE/.venv-exotic"
if "$UV" pip install --quiet --python "$HERE/.venv-exotic/bin/python" -r requirements-exotic.txt; then
  echo "      ok"
else
  echo "      EXOTIC did not install. The other three projects still work. Paste the error above to Claude."
fi

echo "      TRICERATOPS environment (false-positive probabilities, optional) ..."
make_env "$HERE/.venv-fpp"
if "$UV" pip install --quiet --python "$HERE/.venv-fpp/bin/python" -r requirements-fpp.txt; then
  echo "      ok"
else
  rm -rf "$HERE/.venv-fpp"
  echo "      TRICERATOPS did not install. Signals are still checked without it."
fi

echo "[4/4] Checking the code ..."
if [ -d "$HERE/.git" ]; then git -C "$HERE" config core.hooksPath .githooks; fi
"$HERE/.venv/bin/python" -c "import spacelab.server, spacelab.tess_hunter, spacelab.precovery, spacelab.exowatch, spacelab.arxiv_digest; print('      ok')"
cat requirements*.txt | shasum | cut -c1-40 > "$HERE/.venv/.req-stamp"
"$UV" cache clean >/dev/null 2>&1 || true
echo "Space Lab is ready."
