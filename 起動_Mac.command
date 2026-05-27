#!/bin/bash
cd "$(dirname "$0")"
echo "obc_to_freee GUI を起動します..."
python3 obc_to_freee_gui.py
echo ""
echo "サーバーが停止しました。このウィンドウを閉じてください。"
read -p "Enter キーで終了..."
