#!/usr/bin/env python3
"""
obc_to_freee_gui.py
奉行 CSV → freee 変換ツールのウェブ GUI バックエンド

依存: Python 標準ライブラリのみ (pip install 不要)
起動: python3 obc_to_freee_gui.py
      http://127.0.0.1:8765/ がブラウザで自動的に開きます
"""

import argparse
import io
import json
import mimetypes
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from http.server import HTTPServer
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

PORT = 8765
STORAGE_BASE = pathlib.Path.home() / ".obc_to_freee_gui"
UPLOADS_DIR = STORAGE_BASE / "uploads"
OUTPUTS_DIR = STORAGE_BASE / "outputs"
HISTORY_DIR = STORAGE_BASE / "history"
SETTINGS_FILE = STORAGE_BASE / "settings.json"

SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
GUI_DIR = SCRIPT_DIR / "gui"

DEFAULT_SETTINGS = {
    "outputPrefix": "freee用_仕訳データ_obc変換",
    "rowsPerFile": 10000,
    "outputPartners": False,
    "partnersPrefix": "freee取引先マスタ",
    "encoding": "auto",
}

UPLOAD_TTL_DAYS = 7  # デフォルト TTL (--upload-ttl-days で変更可)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
TOKEN_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$')

# ---------------------------------------------------------------------------
# ストレージ初期化
# ---------------------------------------------------------------------------

def cleanup_old_dirs(base_dir: pathlib.Path, ttl_days: int):
    """base_dir 配下の <timestamp>/ ディレクトリを TTL 日数より古ければ削除する。"""
    if not base_dir.exists():
        return
    cutoff = datetime.now() - timedelta(days=ttl_days)
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                print(f"[GUI] TTL クリーンアップ: {entry} (mtime={mtime.date()})")
        except Exception as e:
            print(f"[GUI] TTL クリーンアップ失敗: {entry}: {e}")


def setup_storage_dirs(ttl_days: int = UPLOAD_TTL_DAYS):
    """~/.obc_to_freee_gui/ 配下のディレクトリを作成し、TTL クリーンアップを実行"""
    for d in [UPLOADS_DIR, OUTPUTS_DIR, HISTORY_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    # 機密 CSV を含むアップロード・出力ディレクトリを TTL でクリーンアップ
    cleanup_old_dirs(UPLOADS_DIR, ttl_days)
    cleanup_old_dirs(OUTPUTS_DIR, ttl_days)


# ---------------------------------------------------------------------------
# 設定 読み書き
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # デフォルトとマージ (新キーを補完)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(data: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 監査ログ抽出
# ---------------------------------------------------------------------------

def extract_audit_sections(stderr: str) -> dict:
    """
    stderr から [要確認 X] セクションを抽出して dict で返す。
    各セクションは次の === 行 (または文末) まで。
    """
    result = {
        "auditA": [],
        "auditB": [],
        "balanceWarnings": [],
        "negativeWarnings": [],
    }

    section_map = {
        "[要確認 1/2]": "balanceWarnings",
        "[要確認 2/2]": "negativeWarnings",
        "[要確認 取引先マスタ A]": "auditA",
        "[要確認 取引先マスタ B]": "auditB",
    }

    lines = stderr.splitlines()
    current_key = None
    for line in lines:
        stripped = line.strip()
        # セクション開始チェック
        matched = False
        for marker, key in section_map.items():
            if marker in stripped:
                current_key = key
                matched = True
                break
        if matched:
            continue
        # セクション区切り (===) → リセット
        if stripped.startswith("==="):
            current_key = None
            continue
        # 内容行
        if current_key and stripped:
            result[current_key].append(stripped)

    return result


# ---------------------------------------------------------------------------
# dedup 監査ログ抽出
# ---------------------------------------------------------------------------

def extract_dedup_audit(stderr: str) -> list:
    """
    stderr から [同名異コード対処結果] セクションを抽出してリストで返す。
    各エントリ: {"name": str, "oldCodes": [str], "action": str, "result": str}
    """
    entries = []
    lines = stderr.splitlines()
    in_dedup = False
    current = None

    for line in lines:
        stripped = line.strip()
        if "[同名異コード対処結果]" in stripped:
            in_dedup = True
            continue
        if not in_dedup:
            continue
        # セクション終端
        if stripped.startswith("===") and in_dedup:
            if current:
                entries.append(current)
                current = None
            in_dedup = False
            continue
        # 取引先名行 (末尾 ":")
        if stripped.endswith(":") and not stripped.startswith("元:") and not stripped.startswith("選択:") and not stripped.startswith("結果:"):
            if current:
                entries.append(current)
            current = {"name": stripped[:-1], "oldCodes": [], "action": "", "result": ""}
        elif current and stripped.startswith("元:"):
            # 元: コード XXX / YYY
            codes_part = stripped[len("元:"):].strip()
            if codes_part.startswith("コード "):
                codes_part = codes_part[len("コード "):]
            current["oldCodes"] = [c.strip() for c in codes_part.split("/")]
        elif current and stripped.startswith("選択:"):
            current["action"] = stripped[len("選択:"):].strip()
        elif current and stripped.startswith("結果:"):
            current["result"] = stripped[len("結果:"):].strip()

    if current:
        entries.append(current)

    return entries


# ---------------------------------------------------------------------------
# 出力ファイル情報収集
# ---------------------------------------------------------------------------

def collect_output_files(output_dir: str, partners_prefix: str) -> list:
    """出力ディレクトリの CSV ファイルを列挙して情報を返す"""
    outputs = []
    out_path = pathlib.Path(output_dir)
    if not out_path.exists():
        return outputs

    for csv_file in sorted(out_path.glob("*.csv")):
        size = csv_file.stat().st_size
        # 行数カウント (ヘッダー除く)
        try:
            with open(csv_file, "r", encoding="utf-8-sig", errors="replace") as f:
                row_count = sum(1 for _ in f) - 1  # ヘッダー1行分を引く
            row_count = max(0, row_count)
        except Exception:
            row_count = -1

        # 種別判定
        file_type = "partner" if csv_file.name.startswith(partners_prefix) else "slip"

        outputs.append({
            "filename": csv_file.name,
            "path": str(csv_file.resolve()),
            "type": file_type,
            "rows": row_count,
            "size": size,
        })
    return outputs


# ---------------------------------------------------------------------------
# サマリ計算
# ---------------------------------------------------------------------------

def build_summary(outputs: list, audit: dict) -> dict:
    total_slip_rows = 0
    total_partner_count = 0
    slip_files = 0
    for o in outputs:
        if o["type"] == "slip":
            total_slip_rows += o["rows"]
            slip_files += 1
        elif o["type"] == "partner":
            total_partner_count += o["rows"]

    return {
        "totalSlipRows": total_slip_rows,
        "totalPartnerCount": total_partner_count,
        "slipFiles": slip_files,
        "auditA": audit.get("auditA", []),
        "auditB": audit.get("auditB", []),
        "balanceWarnings": audit.get("balanceWarnings", []),
        "negativeWarnings": audit.get("negativeWarnings", []),
    }


# ---------------------------------------------------------------------------
# リクエストハンドラ
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # デフォルトの CLF ログを抑制し、自前のフォーマットに変える
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        port = self.server.server_address[1]
        self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{port}")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, filepath: pathlib.Path, content_type: str):
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._send_404()

    def _send_404(self):
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def _send_403(self):
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"403 Forbidden")

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._handle_get_index()
        elif path.startswith("/gui/"):
            self._handle_get_static(path[5:])  # "/gui/" 以降
        elif path == "/api/settings":
            self._handle_get_settings()
        elif path == "/api/history":
            self._handle_get_history()
        elif path == "/api/download":
            qs = parse_qs(parsed.query)
            file_path = qs.get("path", [None])[0]
            self._handle_get_download(file_path)
        else:
            self._send_404()

    def _handle_get_index(self):
        index_path = GUI_DIR / "index.html"
        if not index_path.exists():
            # GUI ファイルがない場合は簡易 HTML を返す
            body = self._placeholder_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_file(index_path, "text/html; charset=utf-8")

    def _placeholder_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>obc_to_freee GUI</title></head>
<body>
<h1>obc_to_freee GUI</h1>
<p>GUI ファイル (gui/index.html) が見つかりません。</p>
<p>gui/ ディレクトリに index.html, style.css, app.js を配置してください。</p>
<p><a href="/api/settings">設定確認 (API)</a> | <a href="/api/history">履歴確認 (API)</a></p>
</body>
</html>"""

    def _handle_get_static(self, rel_path: str):
        # ディレクトリトラバーサル防御
        if ".." in rel_path or rel_path.startswith("/"):
            self._send_403()
            return
        file_path = GUI_DIR / rel_path
        # GUI_DIR 配下にあるか確認
        try:
            file_path.resolve().relative_to(GUI_DIR.resolve())
        except ValueError:
            self._send_403()
            return
        if not file_path.exists():
            self._send_404()
            return
        ct, _ = mimetypes.guess_type(str(file_path))
        if ct is None:
            ct = "application/octet-stream"
        if ct.startswith("text/"):
            ct += "; charset=utf-8"
        self._send_file(file_path, ct)

    def _handle_get_settings(self):
        settings = load_settings()
        self._send_json(settings)
        print(f"[GUI] GET /api/settings")

    def _handle_get_history(self):
        records = []
        if HISTORY_DIR.exists():
            history_files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:20]
            for hf in history_files:
                try:
                    with open(hf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # 軽量サマリのみ返す
                    records.append({
                        "timestamp": data.get("timestamp"),
                        "summary": data.get("summary"),
                        "outputCount": len(data.get("outputs", [])),
                        "success": data.get("success"),
                        "exitCode": data.get("exitCode"),
                    })
                except Exception:
                    pass
        self._send_json(records)
        print(f"[GUI] GET /api/history: {len(records)} records")

    def _handle_get_download(self, file_path: str):
        if not file_path:
            self._send_403()
            return
        target = pathlib.Path(file_path).resolve()
        # OUTPUTS_DIR 配下のみ許可
        try:
            target.relative_to(OUTPUTS_DIR.resolve())
        except ValueError:
            self._send_403()
            print(f"[GUI] 拒否: ダウンロードパスが outputs/ 外: {file_path}")
            return
        if not target.exists():
            self._send_404()
            return
        try:
            with open(target, "rb") as f:
                data = f.read()
            filename = target.name
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            # RFC 5987 形式でファイル名をエンコード
            from urllib.parse import quote
            encoded_filename = quote(filename, safe="")
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{encoded_filename}"
            )
            self.end_headers()
            self.wfile.write(data)
            print(f"[GUI] GET /api/download: {filename} ({len(data)} bytes)")
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/convert":
            self._handle_post_convert()
        elif path == "/api/upload-and-detect":
            self._handle_post_upload_and_detect()
        elif path == "/api/convert-with-choices":
            self._handle_post_convert_with_choices()
        elif path == "/api/settings":
            self._handle_post_settings()
        elif path == "/api/shutdown":
            self._handle_post_shutdown()
        else:
            self._send_404()

    def _parse_multipart(self):
        """
        Python 3.13 互換 multipart/form-data パーサー (email.parser 使用)。
        戻り値: (fields_dict, files_list)
          fields_dict: { name: str_value }
          files_list:  [{ "filename": str, "data": bytes }]
        """
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)

        # email.parser で解析するため MIME ヘッダーを前置する
        mime_header = f"Content-Type: {content_type}\r\n\r\n".encode("latin-1")
        msg = BytesParser().parsebytes(mime_header + raw_body)

        fields = {}
        files = []

        for part in msg.get_payload():
            if not hasattr(part, "get_param"):
                continue
            disposition = part.get("Content-Disposition", "")
            name = part.get_param("name", header="Content-Disposition")
            filename = part.get_param("filename", header="Content-Disposition")

            payload = part.get_payload(decode=True)
            if payload is None:
                payload = b""

            if filename:
                files.append({"filename": filename, "data": payload})
            elif name:
                try:
                    fields[name] = payload.decode("utf-8", errors="replace")
                except Exception:
                    fields[name] = ""

        return fields, files

    def _handle_post_convert(self):
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"success": False, "error": "multipart/form-data が必要です"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > MAX_UPLOAD_BYTES:
                self._send_json(
                    {"success": False, "error": f"アップロードサイズが上限 ({MAX_UPLOAD_BYTES // 1024 // 1024}MB) を超えています"},
                    status=413,
                )
                return

            fields, files = self._parse_multipart()

            # フォームフィールド取得
            output_prefix = fields.get("outputPrefix", "freee用_仕訳データ_obc変換").strip() or "freee用_仕訳データ_obc変換"
            rows_per_file = max(100, int(fields.get("rowsPerFile", "10000").strip() or 10000))
            date_from = fields.get("dateFrom", "").strip()
            date_to = fields.get("dateTo", "").strip()
            output_partners_str = fields.get("outputPartners", "false").strip()
            output_partners = output_partners_str.lower() in ("true", "1")
            partners_prefix = fields.get("partnersPrefix", "freee取引先マスタ").strip() or "freee取引先マスタ"
            encoding = fields.get("encoding", "auto").strip() or "auto"
            dedup_strategy = fields.get("dedupStrategy", "warn-only").strip() or "warn-only"
            non_taxable_strategy = fields.get("nonTaxableStrategy", "warn-only").strip() or "warn-only"

            input_file_data = files  # [{ "filename": str, "data": bytes }]

            print(f"[GUI] POST /api/convert: {len(input_file_data)} files, prefix={output_prefix}, dedup={dedup_strategy}, non_taxable={non_taxable_strategy}")

            if not input_file_data:
                self._send_json({"success": False, "error": "入力ファイルが指定されていません"})
                return

            # タイムスタンプ
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            # 一時保存先
            upload_dir = UPLOADS_DIR / timestamp
            upload_dir.mkdir(parents=True, exist_ok=True)

            # 出力先
            output_dir = OUTPUTS_DIR / timestamp
            output_dir.mkdir(parents=True, exist_ok=True)

            # ファイル書き出し
            input_paths = []
            for i, file_info in enumerate(input_file_data):
                fname = file_info.get("filename") or f"input_{i:03d}.csv"
                file_bytes = file_info.get("data", b"")
                # パス安全化
                safe_fname = pathlib.Path(fname).name or f"input_{i:03d}.csv"
                save_path = upload_dir / safe_fname
                with open(save_path, "wb") as f:
                    f.write(file_bytes)
                input_paths.append(str(save_path))

            # subprocess コマンド構築
            cmd = [
                sys.executable,
                str(SCRIPT_DIR / "obc_to_freee.py"),
                "--input", *input_paths,
                "--output-dir", str(output_dir),
                "--output-prefix", output_prefix,
                "--rows-per-file", str(rows_per_file),
                "--encoding", encoding,
            ]
            if date_from:
                cmd += ["--from", date_from]
            if date_to:
                cmd += ["--to", date_to]
            if output_partners:
                cmd.append("--output-partners")
                cmd += ["--partners-prefix", partners_prefix]
                valid_strategies = {"warn-only", "interactive", "merge-lowest", "merge-highest", "merge-most-used", "suffix", "custom"}
                if dedup_strategy in valid_strategies and dedup_strategy != "warn-only":
                    cmd += ["--dedup-strategy", dedup_strategy]

            # 非課税+税額矛盾の戦略 (warn-only/zero-tax/change-to-taxable のみ受け付け、interactive/custom は別フローで)
            valid_nt_strategies = {"warn-only", "zero-tax", "change-to-taxable"}
            if non_taxable_strategy in valid_nt_strategies and non_taxable_strategy != "warn-only":
                cmd += ["--non-taxable-mismatch-strategy", non_taxable_strategy]

            print(f"[GUI] 実行: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(SCRIPT_DIR),
            )

            # 監査ログ抽出
            audit = extract_audit_sections(result.stderr)

            # dedup 監査ログ抽出
            dedup_audit = extract_dedup_audit(result.stderr)

            # 出力ファイル収集
            outputs = collect_output_files(str(output_dir), partners_prefix)

            # サマリ
            summary = build_summary(outputs, audit)

            response = {
                "success": result.returncode == 0,
                "timestamp": timestamp,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exitCode": result.returncode,
                "outputs": outputs,
                "summary": summary,
                "dedupAudit": dedup_audit,
            }

            # 失敗時でも詳細情報を付与
            if result.returncode != 0:
                response["error"] = result.stderr.strip() or "変換処理でエラーが発生しました"

            # ヒストリ保存
            hist_file = HISTORY_DIR / f"{timestamp}.json"
            try:
                with open(hist_file, "w", encoding="utf-8") as f:
                    json.dump(response, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[GUI] ヒストリ保存失敗: {e}")

            self._send_json(response)
            print(f"[GUI] 変換完了: exitCode={result.returncode}, outputs={len(outputs)}")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[GUI] /api/convert エラー: {e}\n{tb}")
            self._send_json({"success": False, "error": str(e), "stderr": tb})

    def _handle_post_upload_and_detect(self):
        """
        POST /api/upload-and-detect
        ファイルをアップロードしてトークンとして保存し、同名異コードを検出して返す。
        custom 戦略の Phase 1 で使用。
        """
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self._send_json({"success": False, "error": "multipart/form-data が必要です"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > MAX_UPLOAD_BYTES:
                self._send_json(
                    {"success": False, "error": f"アップロードサイズが上限 ({MAX_UPLOAD_BYTES // 1024 // 1024}MB) を超えています"},
                    status=413,
                )
                return

            fields, files = self._parse_multipart()

            if not files:
                self._send_json({"success": False, "error": "入力ファイルが指定されていません"})
                return

            # トークン (タイムスタンプ) 発行
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            upload_dir = UPLOADS_DIR / timestamp
            upload_dir.mkdir(parents=True, exist_ok=True)

            # フォームオプション保存
            date_from = fields.get("dateFrom", "").strip()
            date_to = fields.get("dateTo", "").strip()
            encoding = fields.get("encoding", "auto").strip() or "auto"
            output_prefix = fields.get("outputPrefix", "freee用_仕訳データ_obc変換").strip() or "freee用_仕訳データ_obc変換"
            rows_per_file = max(100, int(fields.get("rowsPerFile", "10000").strip() or 10000))
            output_partners_str = fields.get("outputPartners", "false").strip()
            output_partners = output_partners_str.lower() in ("true", "1")
            partners_prefix = fields.get("partnersPrefix", "freee取引先マスタ").strip() or "freee取引先マスタ"

            # ファイル保存
            input_paths = []
            for i, file_info in enumerate(files):
                fname = file_info.get("filename") or f"input_{i:03d}.csv"
                safe_fname = pathlib.Path(fname).name or f"input_{i:03d}.csv"
                save_path = upload_dir / safe_fname
                with open(save_path, "wb") as f:
                    f.write(file_info.get("data", b""))
                input_paths.append(str(save_path))

            # オプションを token メタデータとして保存
            meta = {
                "timestamp": timestamp,
                "inputPaths": input_paths,
                "dateFrom": date_from,
                "dateTo": date_to,
                "encoding": encoding,
                "outputPrefix": output_prefix,
                "rowsPerFile": rows_per_file,
                "outputPartners": output_partners,
                "partnersPrefix": partners_prefix,
            }
            meta_path = upload_dir / "_meta.json"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            # --detect-duplicates-only で同名異コード検出
            cmd = [
                sys.executable,
                str(SCRIPT_DIR / "obc_to_freee.py"),
                "--input", *input_paths,
                "--output-dir", str(upload_dir),
                "--output-prefix", "detect_tmp",
                "--encoding", encoding,
                "--detect-duplicates-only",
            ]
            if date_from:
                cmd += ["--from", date_from]
            if date_to:
                cmd += ["--to", date_to]

            print(f"[GUI] POST /api/upload-and-detect: token={timestamp}, files={len(input_paths)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(SCRIPT_DIR),
            )

            # stdout から JSON パース
            # --detect-duplicates-only は先頭に「読み込み: ...」等のログ行を出力するため、
            # { が始まる位置から JSON を抽出する (--detect-non-taxable-only と同様の処理)
            duplicates = []
            if result.returncode == 0 and result.stdout.strip():
                try:
                    stdout_text = result.stdout.strip()
                    json_start = stdout_text.find("{")
                    if json_start >= 0:
                        detect_result = json.loads(stdout_text[json_start:])
                        duplicates = detect_result.get("duplicates", [])
                except Exception as e:
                    print(f"[GUI] detect JSON パース失敗: {e}")

            # --detect-non-taxable-only で非課税+税額矛盾を検出
            cmd_nt = [
                sys.executable,
                str(SCRIPT_DIR / "obc_to_freee.py"),
                "--input", *input_paths,
                "--output-dir", str(upload_dir),
                "--output-prefix", "detect_tmp_nt",
                "--encoding", encoding,
                "--detect-non-taxable-only",
            ]
            if date_from:
                cmd_nt += ["--from", date_from]
            if date_to:
                cmd_nt += ["--to", date_to]
            result_nt = subprocess.run(
                cmd_nt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(SCRIPT_DIR),
            )
            non_taxable_mismatches = []
            if result_nt.returncode == 0 and result_nt.stdout.strip():
                try:
                    # detect-non-taxable-only は stdout 末尾に JSON を出力 (前段に進捗ログあり)
                    # JSON 部分のみ抽出 ({ から始まる行を探す)
                    stdout_text = result_nt.stdout.strip()
                    json_start = stdout_text.find("{")
                    if json_start >= 0:
                        nt_result = json.loads(stdout_text[json_start:])
                        non_taxable_mismatches = nt_result.get("mismatches", [])
                except Exception as e:
                    print(f"[GUI] detect non-taxable JSON パース失敗: {e}")

            self._send_json({
                "success": True,
                "uploadToken": timestamp,
                "duplicates": duplicates,
                "nonTaxableMismatches": non_taxable_mismatches,
            })
            print(f"[GUI] upload-and-detect 完了: 同名異コード {len(duplicates)} 件 / 非課税矛盾 {len(non_taxable_mismatches)} 件")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[GUI] /api/upload-and-detect エラー: {e}\n{tb}")
            self._send_json({"success": False, "error": str(e)})

    def _handle_post_convert_with_choices(self):
        """
        POST /api/convert-with-choices
        upload-and-detect で発行したトークンと dedup 選択 JSON を受け取って変換実行。
        custom 戦略の Phase 3 で使用。

        リクエストボディ (JSON):
        {
            "uploadToken": "2024-01-01_00-00-00",
            "dedupChoices": {
                "取引先名": {"action": "merge", "target_code": "742001"},
                ...
            }
        }
        """
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            # JSON ボディは通常数 KB 程度。10MB を超える場合は不正リクエストとして拒否
            if content_length > 10 * 1024 * 1024:
                self._send_json({
                    "success": False,
                    "error": f"リクエストサイズが上限 (10MB) を超えています ({content_length // 1024 // 1024}MB)"
                }, status=413)
                return
            body = self.rfile.read(content_length)
            req = json.loads(body.decode("utf-8"))

            upload_token = req.get("uploadToken", "").strip()
            dedup_choices = req.get("dedupChoices", {})
            non_taxable_choices = req.get("nonTaxableChoices", {})

            if not upload_token:
                self._send_json({"success": False, "error": "uploadToken が必要です"})
                return

            # uploadToken 形式バリデーション (YYYY-MM-DD_HH-MM-SS)
            if not TOKEN_PATTERN.match(upload_token):
                self._send_json({"success": False, "error": "不正な uploadToken 形式です"})
                return

            # メタデータ読み込み (パストラバーサル対策: upload_token が UPLOADS_DIR 配下にあるか検証)
            upload_dir = (UPLOADS_DIR / upload_token).resolve()
            try:
                upload_dir.relative_to(UPLOADS_DIR.resolve())
            except ValueError:
                self._send_json({"success": False, "error": "不正な uploadToken です"})
                return
            meta_path = upload_dir / "_meta.json"
            if not meta_path.exists():
                self._send_json({"success": False, "error": f"トークン '{upload_token}' が見つかりません"})
                return

            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            input_paths = meta["inputPaths"]
            date_from = meta.get("dateFrom", "")
            date_to = meta.get("dateTo", "")
            encoding = meta.get("encoding", "auto")
            output_prefix = meta.get("outputPrefix", "freee用_仕訳データ_obc変換")
            rows_per_file = meta.get("rowsPerFile", 10000)
            output_partners = meta.get("outputPartners", False)
            partners_prefix = meta.get("partnersPrefix", "freee取引先マスタ")

            # 出力先
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            output_dir = OUTPUTS_DIR / timestamp
            output_dir.mkdir(parents=True, exist_ok=True)

            # custom JSON を一時ファイルに書き出し
            custom_json_path = upload_dir / "_custom_choices.json"
            with open(custom_json_path, "w", encoding="utf-8") as f:
                json.dump(dedup_choices, f, ensure_ascii=False, indent=2)

            # 非課税 custom JSON を一時ファイルに書き出し
            nt_custom_json_path = upload_dir / "_non_taxable_choices.json"
            with open(nt_custom_json_path, "w", encoding="utf-8") as f:
                json.dump(non_taxable_choices, f, ensure_ascii=False, indent=2)

            # subprocess コマンド構築
            cmd = [
                sys.executable,
                str(SCRIPT_DIR / "obc_to_freee.py"),
                "--input", *input_paths,
                "--output-dir", str(output_dir),
                "--output-prefix", output_prefix,
                "--rows-per-file", str(rows_per_file),
                "--encoding", encoding,
            ]
            if date_from:
                cmd += ["--from", date_from]
            if date_to:
                cmd += ["--to", date_to]
            if output_partners:
                cmd.append("--output-partners")
                cmd += ["--partners-prefix", partners_prefix]
                cmd += ["--dedup-strategy", "custom"]
                cmd += ["--dedup-custom-json", str(custom_json_path)]

            # 非課税 custom 選択がある場合は適用
            if non_taxable_choices:
                cmd += ["--non-taxable-mismatch-strategy", "custom"]
                cmd += ["--non-taxable-mismatch-custom-json", str(nt_custom_json_path)]

            print(f"[GUI] POST /api/convert-with-choices: token={upload_token}, dedup={len(dedup_choices)}, non_taxable={len(non_taxable_choices)}")
            print(f"[GUI] 実行: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(SCRIPT_DIR),
            )

            # 監査ログ抽出
            audit = extract_audit_sections(result.stderr)

            # dedup 監査ログ抽出 (stderr から)
            dedup_audit = extract_dedup_audit(result.stderr)

            # 出力ファイル収集
            outputs = collect_output_files(str(output_dir), partners_prefix)

            # サマリ
            summary = build_summary(outputs, audit)

            response = {
                "success": result.returncode == 0,
                "timestamp": timestamp,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exitCode": result.returncode,
                "outputs": outputs,
                "summary": summary,
                "dedupAudit": dedup_audit,
            }

            if result.returncode != 0:
                response["error"] = result.stderr.strip() or "変換処理でエラーが発生しました"

            # ヒストリ保存
            hist_file = HISTORY_DIR / f"{timestamp}.json"
            try:
                with open(hist_file, "w", encoding="utf-8") as f:
                    json.dump(response, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[GUI] ヒストリ保存失敗: {e}")

            self._send_json(response)
            print(f"[GUI] convert-with-choices 完了: exitCode={result.returncode}, outputs={len(outputs)}")

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[GUI] /api/convert-with-choices エラー: {e}\n{tb}")
            self._send_json({"success": False, "error": str(e), "stderr": tb})

    def _handle_post_settings(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            save_settings(data)
            self._send_json({"success": True})
            print(f"[GUI] POST /api/settings: 保存完了")
        except Exception as e:
            self._send_json({"success": False, "error": str(e)})

    def _handle_post_shutdown(self):
        self._send_json({"success": True, "message": "サーバーを停止します"})
        print("\n[GUI] シャットダウンリクエストを受信しました")
        # 少し待ってからプロセス終了
        def _shutdown():
            time.sleep(0.5)
            os._exit(0)
        t = threading.Thread(target=_shutdown, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # OPTIONS (CORS プリフライト)
    # ------------------------------------------------------------------

    def do_OPTIONS(self):
        port = self.server.server_address[1]
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{port}")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ---------------------------------------------------------------------------
# ThreadingHTTPServer
# ---------------------------------------------------------------------------

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="obc_to_freee GUI バックエンドサーバー"
    )
    parser.add_argument("--port", type=int, default=PORT, help=f"ポート番号 (デフォルト: {PORT})")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="ブラウザを自動で開かない",
    )
    parser.add_argument(
        "--upload-ttl-days",
        type=int,
        default=UPLOAD_TTL_DAYS,
        help=f"uploads/outputs ディレクトリの保持日数 (デフォルト: {UPLOAD_TTL_DAYS} 日)",
    )
    args = parser.parse_args()

    # ストレージディレクトリ作成 + TTL クリーンアップ
    setup_storage_dirs(ttl_days=max(1, args.upload_ttl_days))

    # サーバー起動
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)

    print(f"[GUI] サーバー起動: http://127.0.0.1:{args.port}/")
    print(f"[GUI] 停止するにはこのウィンドウで Ctrl+C を押すか、ブラウザの「停止」ボタンをクリック")
    print(f"[GUI] データ保存先: {STORAGE_BASE}")

    if not args.no_browser:
        threading.Timer(
            1.5,
            lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/"),
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[GUI] 停止します")
        server.shutdown()


if __name__ == "__main__":
    main()
