#!/usr/bin/env sh
set -eu

: "${MINERU_MODEL_SOURCE:=local}"
: "${MINERU_API_HOST:=0.0.0.0}"
: "${MINERU_API_PORT:=8000}"
: "${MINERU_DOWNLOAD_MODELS_ON_START:=1}"
: "${MINERU_MODEL_DOWNLOAD_SOURCE:=modelscope}"
: "${MINERU_MODEL_DOWNLOAD_TYPE:=pipeline}"

mkdir -p "$HOME" "$HF_HOME" "$MODELSCOPE_CACHE"

if [ "$MINERU_DOWNLOAD_MODELS_ON_START" = "1" ] && [ ! -f "$HOME/mineru.json" ]; then
  echo "MinerU local model config not found; running mineru-models-download with source=$MINERU_MODEL_DOWNLOAD_SOURCE model_type=$MINERU_MODEL_DOWNLOAD_TYPE" >&2
  MINERU_MODEL_SOURCE="$MINERU_MODEL_DOWNLOAD_SOURCE" mineru-models-download --source "$MINERU_MODEL_DOWNLOAD_SOURCE" --model_type "$MINERU_MODEL_DOWNLOAD_TYPE"
fi

exec mineru-api --host "$MINERU_API_HOST" --port "$MINERU_API_PORT"
