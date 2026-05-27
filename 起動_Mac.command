#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 &>/dev/null; then
    echo ""
    echo "========================================"
    echo "[エラー] Python3 が見つかりません"
    echo "========================================"
    echo ""
    echo "Python3 をインストールしてください:"
    echo "  Mac (Homebrew): brew install python@3.12"
    echo "  公式: https://www.python.org/downloads/"
    echo ""
    read -p "Enter キーで終了..."
    exit 1
fi
echo "obc_to_freee GUI を起動します..."
python3 obc_to_freee_gui.py
echo ""
echo "サーバーが停止しました。このウィンドウを閉じてください。"
read -p "Enter キーで終了..."
