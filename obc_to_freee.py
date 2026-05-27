#!/usr/bin/env python3
"""
obc_to_freee.py
奉行 CSV (CP932 / UTF-8 / UTF-8 BOM) → freee 仕訳インポート 33列拡張テンプレート UTF-8 BOM CSV

エンコーディングは自動判定 (--encoding auto) または明示指定。
カラムはヘッダー名で参照するため、列順序変更に対して堅牢。
必須カラム欠落時はアクション誘導付きエラーで停止、
オプショナルカラム欠落時は警告付きで処理継続。

Usage:
    python3 obc_to_freee.py \
        --input <上.csv> <下.csv> \
        --output-dir /path/to/output \
        --output-prefix freee用_仕訳データ_obc変換 \
        --rows-per-file 10000 \
        --from 2025/08/01 \
        --to 2026/02/28
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict
from datetime import datetime


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

FREEE_HEADER = [
    "[表題行]", "日付", "伝票番号", "決算整理仕訳",
    "借方勘定科目", "借方科目コード", "借方補助科目", "借方取引先", "借方取引先コード",
    "借方部門", "借方品目", "借方メモタグ", "借方セグメント1", "借方セグメント2", "借方セグメント3",
    "借方金額", "借方税区分", "借方税額",
    "貸方勘定科目", "貸方科目コード", "貸方補助科目", "貸方取引先", "貸方取引先コード",
    "貸方部門", "貸方品目", "貸方メモタグ", "貸方セグメント1", "貸方セグメント2", "貸方セグメント3",
    "貸方金額", "貸方税区分", "貸方税額",
    "摘要",
]
assert len(FREEE_HEADER) == 33, f"ヘッダー列数が不正: {len(FREEE_HEADER)}"

# freee 取引先インポート用テンプレート 57 列ヘッダー (公式テンプレートと完全一致)
FREEE_PARTNER_HEADER = [
    "名前（通称）",
    "取引先コード",
    "ショートカット1",
    "ショートカット2",
    "正式名称（帳票出力時に使用される名称）",
    "カナ名称",
    "敬称",
    "事業所種別",
    "地域",
    "郵便番号",
    "都道府県",
    "市区町村・番地",
    "建物名・部屋番号など",
    "電話番号",
    "営業担当者名",
    "営業担当者メールアドレス",
    "請求書送付方法",
    "入力候補",
    "銀行名",
    "銀行名（カナ）",
    "銀行番号",
    "支店名",
    "支店名（カナ）",
    "支店番号",
    "口座種別",
    "口座番号",
    "受取人名",
    "受取人名（カナ）",
    "締め日(支払期日設定)",
    "支払月(支払期日設定)",
    "支払日(支払期日設定)",
    "締め日(入金期日設定)",
    "入金月(入金期日設定)",
    "入金日(入金期日設定)",
    "手数料負担",
    "振込元口座",
    "適格請求書発行事業者（該当する/該当しない）",
    "適格請求書発行事業者の登録番号",
    "取引先担当者敬称",
    "取引先担当者部署",
    "顧客として利用する",
    "見込顧客として利用する",
    "請求先として利用する",
    "入金元として利用する",
    "仕入先として利用する",
    "支払先として利用する",
    "外税/内税",
    "締め日(請求期日設定)",
    "請求予定月(請求期日設定)",
    "請求予定日(請求期日設定)",
    "入金方法",
    "振込手数料負担区分(請求)",
    "支払方法",
    "帳票共有ポータル",
    "従業員として利用する",
    "販売設定の送付先として利用する",
    "調達設定の送付先として利用する",
]
assert len(FREEE_PARTNER_HEADER) == 57, f"取引先ヘッダー列数が不正: {len(FREEE_PARTNER_HEADER)}"

# 奉行の税区分略称 + 税率 → freee の税区分コード
# キー: (税区分略称.strip(), 税率.strip())
# 税率が空・"0" のケースも含む
TAX_MAP = {
    ("課仕入", "10"): "課対仕入10%",
    ("課仕入", "8"):  "課対仕入8%（軽）",
    ("課仕入", "0"):  "対象外",
    ("課仕入", ""):   "対象外",
    ("課売上", "10"): "課税売上10%",
    ("課売上", "8"):  "課税売上8%（軽）",
    ("課売上", "0"):  "対象外",
    ("課売上", ""):   "対象外",
    ("共仕入", "10"): "共対仕入10%",
    ("共仕入", "8"):  "共対仕入8%（軽）",
    ("共仕入", "0"):  "対象外",
    ("共仕入", ""):   "対象外",
    # 免税（非適格インボイス）
    ("課仕免", "10"): "課対仕入（控80）10%",
    ("課仕免", "8"):  "課対仕入（控80）8%（軽）",
    ("課仕免", "0"):  "対象外",
    ("課仕免", ""):   "対象外",
    ("共仕免", "10"): "共対仕入（控80）10%",
    ("共仕免", "8"):  "共対仕入（控80）8%（軽）",
    ("共仕免", "0"):  "対象外",
    ("共仕免", ""):   "対象外",
    # 課税売上返品
    ("課売返", "10"): "課税売返10%",
    ("課売返", "8"):  "課税売返8%（軽）",
    ("課売返", "0"):  "対象外",
    ("課売返", ""):   "対象外",
    # 非課税
    ("非売上", "10"): "非課売上",
    ("非売上", "8"):  "非課売上",
    ("非売上", "0"):  "非課売上",
    ("非売上", ""):   "非課売上",
    ("非仕入", "10"): "非課仕入",
    ("非仕入", "8"):  "非課仕入",
    ("非仕入", "0"):  "非課仕入",
    ("非仕入", ""):   "非課仕入",
    # 対象外 / 空
    ("対象外", ""):   "対象外",
    ("対象外", "10"): "対象外",
    ("対象外", "8"):  "対象外",
    ("対象外", "0"):  "対象外",
    ("", ""):         "対象外",
    ("", "10"):       "対象外",
    ("", "8"):        "対象外",
    ("", "0"):        "対象外",
}

# エラーカテゴリ定数
CAT_TAX = "CAT_TAX"
CAT_AMOUNT = "CAT_AMOUNT"
CAT_BALANCE_UNKNOWN = "CAT_BALANCE_UNKNOWN"
CAT_DATE_FORMAT = "CAT_DATE_FORMAT"

# ---------------------------------------------------------------------------
# 奉行 CSV カラム名定数
# ---------------------------------------------------------------------------

OBC_COL_DATE = "日付"
OBC_COL_SLIP_NO = "伝票No."
OBC_COL_DR_DEPT = "借方部門名"
OBC_COL_DR_KAMOKU = "借方勘定科目名"
OBC_COL_DR_HOJO = "借方補助科目名"
OBC_COL_DR_TAX_LABEL = "借方税区分略称"
OBC_COL_DR_TAX_RATE = "借方税率"
OBC_COL_DR_PARTNER_CODE = "借方取引先コード"
OBC_COL_DR_PARTNER = "借方取引先名"
OBC_COL_DR_AMOUNT = "借方本体金額"
OBC_COL_DR_TAX_AMOUNT = "借方消費税額"
OBC_COL_CR_DEPT = "貸方部門名"
OBC_COL_CR_KAMOKU = "貸方勘定科目名"
OBC_COL_CR_HOJO = "貸方補助科目名"
OBC_COL_CR_TAX_LABEL = "貸方税区分略称"
OBC_COL_CR_TAX_RATE = "貸方税率"
OBC_COL_CR_PARTNER_CODE = "貸方取引先コード"
OBC_COL_CR_PARTNER = "貸方取引先名"
OBC_COL_CR_AMOUNT = "貸方本体金額"
OBC_COL_CR_TAX_AMOUNT = "貸方消費税額"
OBC_COL_SUMMARY = "摘要"

# 必須カラム: これが無いと変換不能
REQUIRED_COLS = [
    OBC_COL_DATE,
    OBC_COL_SLIP_NO,
    OBC_COL_DR_KAMOKU,
    OBC_COL_DR_AMOUNT,
    OBC_COL_CR_KAMOKU,
    OBC_COL_CR_AMOUNT,
]

# オプショナルカラム: 無くても空文字で処理継続
OPTIONAL_COLS = [
    OBC_COL_DR_DEPT,
    OBC_COL_DR_HOJO,
    OBC_COL_DR_TAX_LABEL,
    OBC_COL_DR_TAX_RATE,
    OBC_COL_DR_PARTNER_CODE,
    OBC_COL_DR_PARTNER,
    OBC_COL_DR_TAX_AMOUNT,
    OBC_COL_CR_DEPT,
    OBC_COL_CR_HOJO,
    OBC_COL_CR_TAX_LABEL,
    OBC_COL_CR_TAX_RATE,
    OBC_COL_CR_PARTNER_CODE,
    OBC_COL_CR_PARTNER,
    OBC_COL_CR_TAX_AMOUNT,
    OBC_COL_SUMMARY,
]


# ---------------------------------------------------------------------------
# エラー収集クラス
# ---------------------------------------------------------------------------

class ConversionErrors:
    """変換中に発生した全エラーを収集するクラス。"""

    def __init__(self):
        # 各カテゴリごとにリストでエラー詳細を保持
        # CAT_TAX: {(label, rate): {"count": N, "examples": [(slip_no, date, side), ...],"filename":""}}
        self._tax_map = {}   # key=(label, rate, filename) → dict
        # その他カテゴリ: [{"filename":..., "line_no":..., "slip_no":..., "date":..., "detail":...}]
        self._amount_errors = []
        self._balance_errors = []
        self._date_format_errors = []

    # ---- 追加メソッド ----

    def add_tax(self, label: str, rate: str, filename: str, line_no: int,
                slip_no: str, date: str, side: str):
        k = (label, rate, filename)
        if k not in self._tax_map:
            self._tax_map[k] = {"label": label, "rate": rate, "filename": filename,
                                "count": 0, "examples": []}
        entry = self._tax_map[k]
        entry["count"] += 1
        if len(entry["examples"]) < 3:
            entry["examples"].append((slip_no, date, side))

    def add_amount(self, filename: str, line_no: int, slip_no: str, date: str,
                   col_name: str, raw_val: str):
        self._amount_errors.append({
            "filename": filename, "line_no": line_no,
            "slip_no": slip_no, "date": date,
            "col_name": col_name, "raw_val": raw_val,
        })

    def add_balance(self, slip_no: str, date: str, dr: int, cr: int):
        self._balance_errors.append({
            "slip_no": slip_no, "date": date, "dr": dr, "cr": cr,
        })

    def add_date_format(self, filename: str, line_no: int, slip_no: str, raw_val: str):
        self._date_format_errors.append({
            "filename": filename, "line_no": line_no,
            "slip_no": slip_no, "raw_val": raw_val,
        })

    # ---- 集計 ----

    def has_critical(self) -> bool:
        """CAT_BALANCE_UNKNOWN (変換バグ) が1件以上あれば True。"""
        return len(self._balance_errors) > 0

    def error_event_count(self) -> int:
        """実イベント件数 (分類ごと)。"""
        tax_events = sum(e["count"] for e in self._tax_map.values())
        return (tax_events + len(self._amount_errors) + len(self._balance_errors) +
                len(self._date_format_errors))

    # ---- 整形出力 ----

    def print_report(self, total_input: int, total_output: int):
        """stderr にカテゴリ別エラーレポートとサマリを出力する。"""
        sep = "=" * 70

        categories = []
        if self._tax_map:
            categories.append(CAT_TAX)
        if self._amount_errors:
            categories.append(CAT_AMOUNT)
        if self._balance_errors:
            categories.append(CAT_BALANCE_UNKNOWN)
        if self._date_format_errors:
            categories.append(CAT_DATE_FORMAT)

        total_cats = len(categories)

        for idx, cat in enumerate(categories, start=1):
            print(sep, file=sys.stderr)
            if cat == CAT_TAX:
                self._print_tax(idx, total_cats)
            elif cat == CAT_AMOUNT:
                self._print_amount(idx, total_cats)
            elif cat == CAT_BALANCE_UNKNOWN:
                self._print_balance(idx, total_cats)
            elif cat == CAT_DATE_FORMAT:
                self._print_date_format(idx, total_cats)

        # サマリ
        print(sep, file=sys.stderr)
        print("変換結果サマリ", file=sys.stderr)
        print(sep, file=sys.stderr)
        print(f"  入力行数: {total_input:,}", file=sys.stderr)
        print(f"  出力行数: {total_output:,}", file=sys.stderr)

        err_count = self.error_event_count()
        file_set = set()
        for e in self._tax_map.values():
            file_set.add(e["filename"])
        for e in self._amount_errors:
            file_set.add(e["filename"])
        for e in self._date_format_errors:
            file_set.add(e["filename"])

        if err_count == 0:
            print(f"  検出エラー: 0 件 ✓", file=sys.stderr)
            print("", file=sys.stderr)
            print("  ✅ 出力 CSV を freee へアップロード可能です。", file=sys.stderr)
        else:
            print(f"  検出エラー数: {err_count} 件", file=sys.stderr)
            if file_set:
                print(f"  検出ファイル数: {len(file_set)} ファイル", file=sys.stderr)
            print("", file=sys.stderr)
            print("  ❌ エラーが検出されました。出力 CSV を freee へアップロードする前に", file=sys.stderr)
            print("     上記のネクストアクションを実施してください。", file=sys.stderr)
        print(sep, file=sys.stderr)

    # ---- カテゴリ別出力ヘルパー ----

    def _print_tax(self, idx: int, total: int):
        tax_events = sum(e["count"] for e in self._tax_map.values())
        print(f"[エラー {idx}/{total}] 想定外の税区分が見つかりました", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("奉行原本に変換マッピング未登録の税区分が含まれていました。", file=sys.stderr)
        print("該当行は税区分を「対象外」で仮置きして変換を継続しましたが、", file=sys.stderr)
        print("freee アップロード前に必ず修正が必要です。", file=sys.stderr)
        print("", file=sys.stderr)
        print("【検出パターン】", file=sys.stderr)
        for entry in self._tax_map.values():
            label = entry["label"]
            rate = entry["rate"] if entry["rate"] else "(空)"
            count = entry["count"]
            filename = os.path.basename(entry["filename"])
            print(f"  税区分略称: {repr(label)}   税率: {repr(rate)}   件数: {count} 行",
                  file=sys.stderr)
            ex_parts = []
            for slip_no, date, side in entry["examples"]:
                ex_parts.append(f"No.{slip_no} ({date}, {side})")
            if ex_parts:
                print(f"  → 伝票例: {', '.join(ex_parts)}", file=sys.stderr)
            print(f"  → ファイル: {filename}", file=sys.stderr)
            print("", file=sys.stderr)
        print("【ネクストアクション】", file=sys.stderr)
        print("  以下のいずれかを実施してください:", file=sys.stderr)
        print("", file=sys.stderr)
        print("  方法 A: 奉行側で税区分を訂正する場合 (推奨)", file=sys.stderr)
        print("    奉行で該当伝票の税区分を、freee に存在する区分へ修正してください。", file=sys.stderr)
        print("    修正後、奉行から CSV を再エクスポートして、本スクリプトを再実行。", file=sys.stderr)
        print("", file=sys.stderr)
        print("  方法 B: スクリプト側で新しい区分を受け入れる場合", file=sys.stderr)
        print("    obc_to_freee.py ファイル冒頭の TAX_MAP 辞書に該当エントリを追加:", file=sys.stderr)
        for entry in self._tax_map.values():
            label = entry["label"]
            rate = entry["rate"]
            print(f'        ("{label}", "{rate}"): "freee側の税区分コード",', file=sys.stderr)
        print("    freee 税区分コード一覧:", file=sys.stderr)
        print("      https://support.freee.co.jp/hc/ja/sections/115000302983", file=sys.stderr)
        print("    追加後、再実行してください。", file=sys.stderr)
        print("", file=sys.stderr)
        print("  ⚠️ 仮置き「対象外」のまま freee へアップロードすると消費税申告に支障が", file=sys.stderr)
        print("  出る可能性があります。必ず本番アップロード前に対処してください。", file=sys.stderr)

    def _print_amount(self, idx: int, total: int):
        print(f"[エラー {idx}/{total}] 金額が数値として読めない行があります", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("奉行原本の金額欄に数字以外の文字が含まれている行があります。", file=sys.stderr)
        print("該当行は金額 0 で仮置きして変換を継続しましたが、freee 上で", file=sys.stderr)
        print("当該伝票の金額が 0 円になります。", file=sys.stderr)
        print("", file=sys.stderr)
        print("【検出された行】", file=sys.stderr)
        for e in self._amount_errors:
            filename = os.path.basename(e["filename"])
            print(f"  ファイル: {filename}", file=sys.stderr)
            slip_info = f"伝票No.{e['slip_no']}, 日付 {e['date']}" if e["slip_no"] else f"日付 {e['date']}"
            print(f"  行番号 {e['line_no']} ({slip_info})", file=sys.stderr)
            print(f"    {e['col_name']}: {repr(e['raw_val'])}  ← 数値変換不可", file=sys.stderr)
            print("", file=sys.stderr)
        print("【ネクストアクション】", file=sys.stderr)
        print("  1. 奉行で該当伝票を開き、金額欄を確認してください", file=sys.stderr)
        print("  2. 数値以外の文字 (カンマ・全角数字・記号等) が混入していないか確認", file=sys.stderr)
        print("  3. 修正後、奉行から CSV を再エクスポートして本スクリプトを再実行", file=sys.stderr)

    def _print_balance(self, idx: int, total: int):
        print(f"[エラー {idx}/{total}] 出力 CSV で借貸合計の不一致を検出 (変換バグの可能性)", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("変換ロジック自体に問題があり、奉行原本では一致していた伝票が", file=sys.stderr)
        print("出力 CSV で不一致になっています。これは経理担当の起票ミスではなく", file=sys.stderr)
        print("スクリプトのバグです。出力 CSV はそのまま freee へ上げないでください。", file=sys.stderr)
        print("", file=sys.stderr)
        print("【検出された伝票】", file=sys.stderr)
        for e in self._balance_errors:
            dr = e["dr"]
            cr = e["cr"]
            diff = dr - cr
            print(f"  No.{e['slip_no']} ({e['date']}) 借方合計 {dr:,} / 貸方合計 {cr:,} / 差 {diff:,}",
                  file=sys.stderr)
        print("", file=sys.stderr)
        print("【ネクストアクション】", file=sys.stderr)
        print("  スクリプト開発者へ以下を伝えてください:", file=sys.stderr)
        print("  - 上記の伝票番号と日付", file=sys.stderr)
        print("  - 使用したコマンド (再現用)", file=sys.stderr)
        print("  - スクリプトのバージョン (ファイルのタイムスタンプ)", file=sys.stderr)

    def _print_date_format(self, idx: int, total: int):
        print(f"[エラー {idx}/{total}] 日付フォーマット異常", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("奉行原本の日付欄が YYYY/MM/DD 形式になっていない行があります。", file=sys.stderr)
        print("該当行は日付をそのまま出力して変換を継続していますが、", file=sys.stderr)
        print("freee インポート時に日付エラーになる可能性があります。", file=sys.stderr)
        print("", file=sys.stderr)
        print("【検出された行】", file=sys.stderr)
        for e in self._date_format_errors:
            filename = os.path.basename(e["filename"])
            slip_info = f", 伝票No.{e['slip_no']}" if e["slip_no"] else ""
            print(f"  ファイル: {filename}", file=sys.stderr)
            print(f"  行番号 {e['line_no']}{slip_info}: 日付値 {repr(e['raw_val'])}", file=sys.stderr)
        print("", file=sys.stderr)
        print("【ネクストアクション】", file=sys.stderr)
        print("  奉行で該当伝票の日付を確認し、正しい日付に修正後、", file=sys.stderr)
        print("  奉行から CSV を再エクスポートして本スクリプトを再実行してください。", file=sys.stderr)


# グローバルエラー収集インスタンス (モジュールレベル)
_errors = ConversionErrors()
# 現在処理中のファイル名 (read_obc_csv 内でセット)
_current_file = ""


# ---------------------------------------------------------------------------
# エンコーディング自動判定
# ---------------------------------------------------------------------------

def detect_encoding(filepath: str) -> str:
    """ファイル先頭バイトとデコード試行で奉行 CSV のエンコーディングを判定。

    注意: マルチバイト文字の境界でぶつ切れになるとデコード失敗が誤判定になるため、
    ファイル全体を読んで検証する。
    """
    with open(filepath, 'rb') as f:
        raw = f.read()

    # BOM 判定
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        return 'utf-16'

    # UTF-8 (BOM なし) を試す → 失敗したら CP932
    try:
        raw.decode('utf-8', errors='strict')
        return 'utf-8'
    except UnicodeDecodeError:
        try:
            raw.decode('cp932', errors='strict')
            return 'cp932'
        except UnicodeDecodeError:
            raise ValueError(
                f"ファイル {filepath} のエンコーディングを判定できませんでした。"
                "CP932 / UTF-8 / UTF-8 BOM のいずれかで保存し直してください。"
            )


# ---------------------------------------------------------------------------
# ヘッダーインデックス辞書
# ---------------------------------------------------------------------------

def build_header_index(header_row: list) -> dict:
    """ヘッダー名 → カラムインデックスの辞書を作成。空白除去 + BOM 除去で正規化。"""
    idx = {}
    for i, name in enumerate(header_row):
        normalized = name.strip().lstrip('﻿').strip()
        if normalized:
            idx[normalized] = i
    return idx


def get_col(row: list, header_idx: dict, name: str, default: str = "") -> str:
    """カラム名で値を取得。カラムが存在しない・行が短い場合は default を返す。"""
    if name not in header_idx:
        return default
    i = header_idx[name]
    if i >= len(row):
        return default
    return row[i]


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def normalize_cell(value: str) -> str:
    """セル内改行・連続スペースを除去してstrip()する。"""
    value = re.sub(r"[\r\n]+", " ", value)
    value = re.sub(r" +", " ", value)
    return value.strip()


def map_tax(label: str, rate: str, context: str = "",
            line_no: int = 0, slip_no: str = "", date: str = "",
            side: str = "") -> str:
    """
    奉行の税区分略称と税率をfreee税区分コードに変換する。
    想定外の組み合わせはエラーを収集して仮値「対象外」を返す。
    """
    key = (label.strip(), rate.strip())
    if key not in TAX_MAP:
        _errors.add_tax(
            label=label.strip(),
            rate=rate.strip(),
            filename=_current_file,
            line_no=line_no,
            slip_no=slip_no,
            date=date,
            side=side,
        )
        return "対象外"
    return TAX_MAP[key]


def clean_auxiliary(val: str) -> str:
    """補助科目の「その他」は空欄化。"""
    v = normalize_cell(val)
    return "" if v == "その他" else v


def clean_partner_name(val: str) -> str:
    """取引先名の「その他取引先」および空白のみは空欄化。"""
    v = normalize_cell(val)
    if v in ("その他取引先", ""):
        return ""
    return v


def clean_partner_code(val: str) -> str:
    """取引先コードの「000000」および空白のみは空欄化。"""
    v = normalize_cell(val)
    if v in ("000000", ""):
        return ""
    return v


def clean_bumon(val: str) -> str:
    """部門の「その他」は空欄化。"""
    v = normalize_cell(val)
    return "" if v == "その他" else v


def parse_amount(val: str, col_name: str,
                 line_no: int = 0, slip_no: str = "", date: str = "") -> str:
    """
    金額文字列を検証して返す。
    空白のみ → "0" に正規化。数値変換できなければエラー収集して "0" を返す。
    """
    v = val.strip()
    if v == "":
        return "0"
    try:
        int(v)
    except ValueError:
        try:
            float(v)
        except ValueError:
            _errors.add_amount(
                filename=_current_file,
                line_no=line_no,
                slip_no=slip_no,
                date=date,
                col_name=col_name,
                raw_val=val,
            )
            return "0"
    return v


# ---------------------------------------------------------------------------
# 奉行1行 → freee33列辞書に変換
# ---------------------------------------------------------------------------

def obc_row_to_freee(row: list, header_idx: dict, line_no: int) -> dict:
    """
    奉行1行をfreee33列の辞書に変換する。
    Transformation 2 (普通預金→補助科目置換) を適用。
    カラムはヘッダー名で参照するため列順序不問。
    """
    def gcol(name: str) -> str:
        return get_col(row, header_idx, name)

    slip_no = normalize_cell(gcol(OBC_COL_SLIP_NO))
    date = normalize_cell(gcol(OBC_COL_DATE))

    # --- 借方 ---
    dr_kamoku = normalize_cell(gcol(OBC_COL_DR_KAMOKU))
    dr_hojo = clean_auxiliary(gcol(OBC_COL_DR_HOJO))
    dr_partner = clean_partner_name(gcol(OBC_COL_DR_PARTNER))
    dr_partner_code = clean_partner_code(gcol(OBC_COL_DR_PARTNER_CODE))
    dr_bumon = clean_bumon(gcol(OBC_COL_DR_DEPT))
    dr_amount = parse_amount(gcol(OBC_COL_DR_AMOUNT), OBC_COL_DR_AMOUNT,
                             line_no=line_no, slip_no=slip_no, date=date)
    dr_tax_label = normalize_cell(gcol(OBC_COL_DR_TAX_LABEL))
    dr_tax_rate = normalize_cell(gcol(OBC_COL_DR_TAX_RATE))
    dr_tax_amount = parse_amount(gcol(OBC_COL_DR_TAX_AMOUNT), OBC_COL_DR_TAX_AMOUNT,
                                 line_no=line_no, slip_no=slip_no, date=date)
    dr_tax_code = map_tax(dr_tax_label, dr_tax_rate,
                          context=f"借方 line={line_no}, 伝票No={slip_no}",
                          line_no=line_no, slip_no=slip_no, date=date, side="借方")

    # --- 貸方 ---
    cr_kamoku = normalize_cell(gcol(OBC_COL_CR_KAMOKU))
    cr_hojo = clean_auxiliary(gcol(OBC_COL_CR_HOJO))
    cr_partner = clean_partner_name(gcol(OBC_COL_CR_PARTNER))
    cr_partner_code = clean_partner_code(gcol(OBC_COL_CR_PARTNER_CODE))
    cr_bumon = clean_bumon(gcol(OBC_COL_CR_DEPT))
    cr_amount = parse_amount(gcol(OBC_COL_CR_AMOUNT), OBC_COL_CR_AMOUNT,
                             line_no=line_no, slip_no=slip_no, date=date)
    cr_tax_label = normalize_cell(gcol(OBC_COL_CR_TAX_LABEL))
    cr_tax_rate = normalize_cell(gcol(OBC_COL_CR_TAX_RATE))
    cr_tax_amount = parse_amount(gcol(OBC_COL_CR_TAX_AMOUNT), OBC_COL_CR_TAX_AMOUNT,
                                 line_no=line_no, slip_no=slip_no, date=date)
    cr_tax_code = map_tax(cr_tax_label, cr_tax_rate,
                          context=f"貸方 line={line_no}, 伝票No={slip_no}",
                          line_no=line_no, slip_no=slip_no, date=date, side="貸方")

    # --- 摘要 ---
    summary = normalize_cell(gcol(OBC_COL_SUMMARY))

    # --- Transformation 2: 普通預金→補助科目置換 ---
    if dr_kamoku == "普通預金" and dr_hojo:
        dr_kamoku = dr_hojo
        dr_hojo = ""
    if cr_kamoku == "普通預金" and cr_hojo:
        cr_kamoku = cr_hojo
        cr_hojo = ""

    return {
        "date": date,
        "slip_no": slip_no,
        "dr_kamoku": dr_kamoku,
        "dr_hojo": dr_hojo,
        "dr_partner": dr_partner,
        "dr_partner_code": dr_partner_code,
        "dr_bumon": dr_bumon,
        "dr_amount": dr_amount,
        "dr_tax_code": dr_tax_code,
        "dr_tax_amount": dr_tax_amount,
        "cr_kamoku": cr_kamoku,
        "cr_hojo": cr_hojo,
        "cr_partner": cr_partner,
        "cr_partner_code": cr_partner_code,
        "cr_bumon": cr_bumon,
        "cr_amount": cr_amount,
        "cr_tax_code": cr_tax_code,
        "cr_tax_amount": cr_tax_amount,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Transformation 1: 「複合」補完 (行分解版)
# ---------------------------------------------------------------------------

def apply_fukugo(rows_by_slip: list, group_bumon: str) -> list:
    """
    同一伝票グループのrows_by_slipに対して「行分解」ロジックを適用し、
    変換済みリストを返す。

    奉行の1明細行は借方部分・貸方部分の有効性に応じて以下に分解する:
      - 借方のみ有効: 1行 (借方=実データ, 貸方=複合)
      - 貸方のみ有効: 1行 (借方=複合, 貸方=実データ)
      - 両側有効    : 2行 (借方明細行 + 貸方明細行)
      - 両側無効    : スキップ (警告ログ)

    「複合」行の値:
      - 勘定科目=複合, 補助科目=空, 取引先=空, 取引先コード=空
      - 部門=反対側部門 (なければ group_bumon)
      - 税区分=対象外, 税額=0
      - 金額=実データ側と同額
    """
    result = []
    for r in rows_by_slip:
        debit_active = (r["dr_kamoku"] != "" and r["dr_amount"] != "0")
        credit_active = (r["cr_kamoku"] != "" and r["cr_amount"] != "0")

        if debit_active and not credit_active:
            # 借方のみ → 1行: 借方=実データ, 貸方=複合
            bumon = r["dr_bumon"] if r["dr_bumon"] else group_bumon
            row = dict(r)
            row["cr_kamoku"] = "複合"
            row["cr_hojo"] = ""
            row["cr_bumon"] = bumon
            row["cr_tax_code"] = "対象外"
            row["cr_amount"] = r["dr_amount"]
            row["cr_tax_amount"] = "0"
            row["cr_partner"] = ""
            row["cr_partner_code"] = ""
            result.append(row)

        elif credit_active and not debit_active:
            # 貸方のみ → 1行: 借方=複合, 貸方=実データ
            bumon = r["cr_bumon"] if r["cr_bumon"] else group_bumon
            row = dict(r)
            row["dr_kamoku"] = "複合"
            row["dr_hojo"] = ""
            row["dr_bumon"] = bumon
            row["dr_tax_code"] = "対象外"
            row["dr_amount"] = r["cr_amount"]
            row["dr_tax_amount"] = "0"
            row["dr_partner"] = ""
            row["dr_partner_code"] = ""
            result.append(row)

        elif debit_active and credit_active:
            # 両側有効 → 2行に分解
            bumon_for_cr_side = r["dr_bumon"] if r["dr_bumon"] else group_bumon
            bumon_for_dr_side = r["cr_bumon"] if r["cr_bumon"] else group_bumon

            # 行1: 借方=実データ, 貸方=複合 (借方明細)
            dr_row = dict(r)
            dr_row["cr_kamoku"] = "複合"
            dr_row["cr_hojo"] = ""
            dr_row["cr_bumon"] = bumon_for_cr_side
            dr_row["cr_tax_code"] = "対象外"
            dr_row["cr_amount"] = r["dr_amount"]
            dr_row["cr_tax_amount"] = "0"
            dr_row["cr_partner"] = ""
            dr_row["cr_partner_code"] = ""
            result.append(dr_row)

            # 行2: 借方=複合, 貸方=実データ (貸方明細)
            cr_row = dict(r)
            cr_row["dr_kamoku"] = "複合"
            cr_row["dr_hojo"] = ""
            cr_row["dr_bumon"] = bumon_for_dr_side
            cr_row["dr_tax_code"] = "対象外"
            cr_row["dr_amount"] = r["cr_amount"]
            cr_row["dr_tax_amount"] = "0"
            cr_row["dr_partner"] = ""
            cr_row["dr_partner_code"] = ""
            result.append(cr_row)

        else:
            # 両側無効 (勘定科目空 + 金額0) → 奉行の空パディング行としてスキップ
            # None をマーカーとして追加 (呼び出し元で集計)
            result.append(None)

    # None (スキップ行) を除去して返す。件数カウントのみ返す
    skipped = sum(1 for r in result if r is None)
    result = [r for r in result if r is not None]
    return result, skipped


def find_group_bumon(rows: list) -> str:
    """グループ内の借方・貸方部門からフォールバック部門を探す。"""
    for r in rows:
        if r.get("dr_bumon"):
            return r["dr_bumon"]
    for r in rows:
        if r.get("cr_bumon"):
            return r["cr_bumon"]
    return ""


# ---------------------------------------------------------------------------
# freee33列リストに変換
# ---------------------------------------------------------------------------

def to_freee_row(r: dict,
                 code_rewrite_map: dict = None,
                 name_override_map: dict = None) -> list:
    """辞書をfreee33列リストに変換する。

    code_rewrite_map: {古コード: 新コード, ...} 統合時に取引先コードを書き換える
    name_override_map: {コード: 新名前, ...}   別名化時に取引先名を書き換える
    """
    dr_code = r["dr_partner_code"]
    cr_code = r["cr_partner_code"]
    dr_name = r["dr_partner"]
    cr_name = r["cr_partner"]

    if code_rewrite_map:
        if dr_code in code_rewrite_map:
            dr_code = code_rewrite_map[dr_code]
        if cr_code in code_rewrite_map:
            cr_code = code_rewrite_map[cr_code]

    if name_override_map:
        if dr_code in name_override_map:
            dr_name = name_override_map[dr_code]
        if cr_code in name_override_map:
            cr_name = name_override_map[cr_code]

    return [
        "[明細行]",  # [表題行]
        r["date"],           # 日付
        r["slip_no"],        # 伝票番号
        "",                  # 決算整理仕訳
        r["dr_kamoku"],      # 借方勘定科目
        "",                  # 借方科目コード
        r["dr_hojo"],        # 借方補助科目
        dr_name,             # 借方取引先
        dr_code,             # 借方取引先コード
        r["dr_bumon"],       # 借方部門
        "",                  # 借方品目
        "",                  # 借方メモタグ
        "",                  # 借方セグメント1
        "",                  # 借方セグメント2
        "",                  # 借方セグメント3
        r["dr_amount"],      # 借方金額
        r["dr_tax_code"],    # 借方税区分
        r["dr_tax_amount"],  # 借方税額
        r["cr_kamoku"],      # 貸方勘定科目
        "",                  # 貸方科目コード
        r["cr_hojo"],        # 貸方補助科目
        cr_name,             # 貸方取引先
        cr_code,             # 貸方取引先コード
        r["cr_bumon"],       # 貸方部門
        "",                  # 貸方品目
        "",                  # 貸方メモタグ
        "",                  # 貸方セグメント1
        "",                  # 貸方セグメント2
        "",                  # 貸方セグメント3
        r["cr_amount"],      # 貸方金額
        r["cr_tax_code"],    # 貸方税区分
        r["cr_tax_amount"],  # 貸方税額
        r["summary"],        # 摘要
    ]


# ---------------------------------------------------------------------------
# 奉行CSVの読み込み・変換
# ---------------------------------------------------------------------------

_DATE_PATTERN = re.compile(r"^\d{4}/\d{2}/\d{2}$")


def read_obc_csv(filepath: str, date_from=None, date_to=None,
                 encoding: str = "auto") -> dict:
    """
    奉行 CSV を読み込み、freee 行辞書のリストを返す。
    date_from/date_to が指定されている場合、期間外の伝票を除外する。
    戻り値は OrderedDict 形式: {slip_key: [行辞書, ...], ...}

    encoding: "auto" の場合は detect_encoding() で自動判定、
              それ以外は指定値を直接使用。
    """
    global _current_file
    _current_file = filepath

    # エンコーディング決定
    if encoding == "auto":
        enc = detect_encoding(filepath)
        print(f"  エンコーディング自動判定: {enc}", file=sys.stderr)
    else:
        enc = encoding

    groups = OrderedDict()

    with open(filepath, encoding=enc, errors="replace") as f:
        reader = csv.reader(f)
        try:
            header_row = next(reader)
        except StopIteration:
            return groups

        # ヘッダーインデックス構築
        header_idx = build_header_index(header_row)

        # 必須カラム存在チェック (フェイルファースト)
        missing = [c for c in REQUIRED_COLS if c not in header_idx]
        if missing:
            sep = "=" * 70
            print(sep, file=sys.stderr)
            print(f"[エラー] 必須カラムが見つかりません: {filepath}", file=sys.stderr)
            print(sep, file=sys.stderr)
            print("以下のカラムが奉行 CSV のヘッダー行に見つかりませんでした:", file=sys.stderr)
            for c in missing:
                print(f"  - {c}", file=sys.stderr)
            print("", file=sys.stderr)
            print("検出されたヘッダー:", file=sys.stderr)
            for h in header_row:
                print(f"    {repr(h)}", file=sys.stderr)
            print("", file=sys.stderr)
            print("【ネクストアクション】", file=sys.stderr)
            print("  1. 奉行のエクスポート設定を確認し、上記カラムを含む形式でエクスポートし直す", file=sys.stderr)
            print("  2. もしくは奉行 CSV のヘッダー行を手動で修正し、必須カラム名を統一する", file=sys.stderr)
            print("     カラム名のスペル例: 「借方勘定科目名」「借方本体金額」 (全角)", file=sys.stderr)
            print(sep, file=sys.stderr)
            sys.exit(1)

        # オプショナルカラム欠落警告
        optional_missing = [c for c in OPTIONAL_COLS if c not in header_idx]
        if optional_missing:
            print(f"[注意] 以下のオプショナルカラムは奉行 CSV に存在しないため、"
                  "freee 側で空欄になります:", file=sys.stderr)
            for c in optional_missing:
                print(f"  - {c}", file=sys.stderr)
            # 税区分関連が欠落していれば追加通知
            tax_cols = [OBC_COL_DR_TAX_LABEL, OBC_COL_DR_TAX_RATE,
                        OBC_COL_CR_TAX_LABEL, OBC_COL_CR_TAX_RATE]
            if any(c in optional_missing for c in tax_cols):
                print("  ※ 税区分関連カラムが見つからないため、税区分は「対象外」固定で変換します。",
                      file=sys.stderr)

        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue

            # 日付取得 (ヘッダー名で参照)
            date_str = get_col(row, header_idx, OBC_COL_DATE).strip()
            if not date_str:
                continue

            # 日付フォーマットチェック
            slip_no_raw = get_col(row, header_idx, OBC_COL_SLIP_NO).strip()
            if not _DATE_PATTERN.match(date_str):
                _errors.add_date_format(
                    filename=filepath,
                    line_no=line_no,
                    slip_no=slip_no_raw,
                    raw_val=date_str,
                )

            # 期間フィルタ
            if date_from or date_to:
                try:
                    row_date = datetime.strptime(date_str, "%Y/%m/%d")
                    if date_from and row_date < date_from:
                        continue
                    if date_to and row_date > date_to:
                        continue
                except ValueError:
                    pass  # 日付パース失敗はスキップせず変換を続ける

            # 変換
            d = obc_row_to_freee(row, header_idx, line_no)

            # グループキー: (日付, 伝票No)
            key = (d["date"], d["slip_no"])
            if key not in groups:
                groups[key] = []
            groups[key].append(d)

    return groups


# ---------------------------------------------------------------------------
# グループ処理 (Transformation 1 適用)
# ---------------------------------------------------------------------------

def process_groups(groups: OrderedDict,
                   code_rewrite_map: dict = None,
                   name_override_map: dict = None) -> tuple:
    """
    全グループに複合補完を適用し、(freee33列の行リスト, グループ別行数リスト) を返す。
    グループ別行数リストは [(key, freee行数), ...] の順序付きリスト。
    行分解により1奉行行が2freee行になる場合があるため、
    実際の出力行数を正確に計算する。

    code_rewrite_map / name_override_map が指定された場合は to_freee_row() で適用する。
    """
    all_rows = []
    slip_sizes = []
    total_skipped = 0
    for key, slip_rows in groups.items():
        group_bumon = find_group_bumon(slip_rows)
        completed, skipped = apply_fukugo(slip_rows, group_bumon)
        total_skipped += skipped
        freee_rows = [
            to_freee_row(r, code_rewrite_map=code_rewrite_map,
                         name_override_map=name_override_map)
            for r in completed
        ]
        all_rows.extend(freee_rows)
        slip_sizes.append((key, len(freee_rows)))
    if total_skipped > 0:
        print(
            f"WARN: 借方・貸方ともに空の行 (奉行の空パディング行) を合計 {total_skipped} 件スキップ",
            file=sys.stderr,
        )
    return all_rows, slip_sizes


# ---------------------------------------------------------------------------
# 伝票単位 借貸整合性チェック
# ---------------------------------------------------------------------------

# 奉行原本でも不一致だった伝票 (既知) — デフォルト空。--known-balance-mismatches で指定
KNOWN_MISMATCH_SLIPS: set = set()  # 空。--known-balance-mismatches で指定


def check_slip_balance(all_rows: list, label: str = "",
                       known_mismatch_slips: set = None) -> bool:
    """
    出力行リストを伝票(日付+伝票番号)単位でグループ化し、
    借方金額合計と貸方金額合計を比較する。

    - 既知不一致 (known_mismatch_slips) → WARN: として表示 (継続)
    - それ以外の不一致 → ERROR: として表示、_errors に追加 (is_ok=False)

    Returns:
        bool: 未知の不一致が0件なら True、1件以上なら False
    """
    if known_mismatch_slips is None:
        known_mismatch_slips = KNOWN_MISMATCH_SLIPS

    # インデックス: 借方金額=col15 (0-index), 貸方金額=col29 (0-index)
    # all_rows の各要素は to_freee_row() が返す 33列リスト
    # col index: [表題行]=0, 日付=1, 伝票番号=2, ..., 借方金額=15, 貸方金額=29

    slip_dr = defaultdict(int)
    slip_cr = defaultdict(int)

    for row in all_rows:
        key = (row[1], row[2])  # (日付, 伝票番号)
        try:
            slip_dr[key] += int(str(row[15]).strip() or "0")
        except (ValueError, IndexError):
            pass
        try:
            slip_cr[key] += int(str(row[29]).strip() or "0")
        except (ValueError, IndexError):
            pass

    unknown_mismatches = []
    known_mismatches = []

    for key in sorted(slip_dr.keys()):
        dr = slip_dr[key]
        cr = slip_cr[key]
        if dr != cr:
            date_str, slip_no = key
            if slip_no in known_mismatch_slips:
                known_mismatches.append((date_str, slip_no, dr, cr))
            else:
                unknown_mismatches.append((date_str, slip_no, dr, cr))

    prefix = f"[{label}] " if label else ""

    if known_mismatches:
        print(f"{prefix}既知不一致 (--known-balance-mismatches 指定) ({len(known_mismatches)} 件):", file=sys.stderr)
        for date_str, slip_no, dr, cr in known_mismatches:
            print(f"  WARN: 伝票 {slip_no} 日付 {date_str} 借方合計 {dr} 貸方合計 {cr} 差 {dr - cr}", file=sys.stderr)

    if unknown_mismatches:
        print(f"{prefix}*** 未知の不一致 ({len(unknown_mismatches)} 件) - 変換バグの可能性 ***:", file=sys.stderr)
        for date_str, slip_no, dr, cr in unknown_mismatches:
            print(f"  ERROR: 伝票 {slip_no} 日付 {date_str} 借方合計 {dr} 貸方合計 {cr} 差 {dr - cr}", file=sys.stderr)
            # エラー収集にも追加
            _errors.add_balance(slip_no=slip_no, date=date_str, dr=dr, cr=cr)
        return False

    print(f"{prefix}借貸整合性チェック: 既知不一致 {len(known_mismatches)} 件のみ → PASS", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# 出力CSVの書き込み (伝票境界をまたがないファイル分割)
# ---------------------------------------------------------------------------

def write_output(
    all_rows: list,
    slip_sizes: list,
    output_dir: str,
    prefix: str,
    rows_per_file: int,
):
    """
    freee33列行リストを分割してUTF-8 BOM CSVに書き出す。
    伝票(日付+伝票番号)の途中で分割しない。
    slip_sizes は [(key, freee行数), ...] の形式 (process_groups が返す値)。
    """
    os.makedirs(output_dir, exist_ok=True)

    if not all_rows:
        print("出力対象の行がありません。", file=sys.stderr)
        return []

    file_index = 1
    written_files = []
    row_cursor = 0  # all_rows 内のポインタ
    slip_cursor = 0  # slip_sizes 内のポインタ

    while slip_cursor < len(slip_sizes):
        outpath = os.path.join(output_dir, f"{prefix}_{file_index:03d}.csv")
        file_rows = []
        current_count = 0

        while slip_cursor < len(slip_sizes):
            _, slip_size = slip_sizes[slip_cursor]

            # しきい値に達しているが伝票境界ならファイルを閉じる
            # (ただし1伝票もまだ書いていない場合はそのまま書く)
            if current_count >= rows_per_file and current_count > 0:
                break

            # この伝票の行を追加
            for i in range(slip_size):
                file_rows.append(all_rows[row_cursor + i])
            row_cursor += slip_size
            current_count += slip_size
            slip_cursor += 1

        with open(outpath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(FREEE_HEADER)
            writer.writerows(file_rows)

        written_files.append((outpath, len(file_rows)))
        # 進捗系: 書き出し・完了サマリは stdout (パイプ後処理用)。診断系のみ stderr。
        print(f"  書き出し: {outpath}  ({len(file_rows)} 行)")
        file_index += 1

    return written_files


# ---------------------------------------------------------------------------
# 取引先マスタ収集・出力
# ---------------------------------------------------------------------------

def collect_partners(groups: OrderedDict) -> dict:
    """
    変換済みグループから取引先 (コード, 名前) のユニークリストを収集する。

    戻り値:
        {
          "partners": {code: name, ...},  # 最終採用コード→名前
          "dr_only": set(codes),          # 借方にのみ出現
          "cr_only": set(codes),          # 貸方にのみ出現
          "both": set(codes),             # 両側に出現
          "noise_count": int,             # ノイズ除去件数
          "multi_name_count": int,        # 同コード異名検出件数
          "multi_name_details": [         # 同コード異名詳細
              {"code": ..., "candidates": [(name, count), ...], "adopted": ...}, ...
          ],
        }

    同コード異名検出:
    - 同一取引先コードに対して複数の取引先名が出現した場合、
      出現回数最多 (最頻) の名前を採用する。
    - 出現回数が同数 (tie) の場合は、奉行原本の **出現順 (上のファイルから順、各ファイル内は行番号順) で最初に登場した名前** を採用する
      (Python defaultdict の挿入順序保持に依存する仕様)。
    - tie ケースが発生した取引先は stderr の [要確認 取引先マスタ A] セクションに全候補が列挙されるため、経理担当が手動で確認可能。
    """
    # code -> {name: count}
    dr_map: dict = defaultdict(lambda: defaultdict(int))
    cr_map: dict = defaultdict(lambda: defaultdict(int))
    noise_count = 0

    for key, rows in groups.items():
        for r in rows:
            # 借方
            dr_code_raw = r.get("dr_partner_code", "")
            dr_name_raw = r.get("dr_partner", "")
            dr_code = dr_code_raw.strip()
            dr_name = dr_name_raw.strip()

            if dr_code and dr_code != "000000" and dr_name and dr_name != "その他取引先":
                dr_map[dr_code][dr_name] += 1
            elif dr_code or dr_name:
                # 片方だけ有効 → ノイズ
                noise_count += 1

            # 貸方
            cr_code_raw = r.get("cr_partner_code", "")
            cr_name_raw = r.get("cr_partner", "")
            cr_code = cr_code_raw.strip()
            cr_name = cr_name_raw.strip()

            if cr_code and cr_code != "000000" and cr_name and cr_name != "その他取引先":
                cr_map[cr_code][cr_name] += 1
            elif cr_code or cr_name:
                noise_count += 1

    # 全コードをマージ (借方 + 貸方)
    all_codes = set(dr_map.keys()) | set(cr_map.keys())
    dr_codes = set(dr_map.keys())
    cr_codes = set(cr_map.keys())
    dr_only = dr_codes - cr_codes
    cr_only = cr_codes - dr_codes
    both = dr_codes & cr_codes

    merged: dict = defaultdict(lambda: defaultdict(int))
    for code, names in dr_map.items():
        for name, cnt in names.items():
            merged[code][name] += cnt
    for code, names in cr_map.items():
        for name, cnt in names.items():
            merged[code][name] += cnt

    # 同コード異名検出・最頻名採用
    partners = {}
    multi_name_details = []

    for code in sorted(all_codes):
        name_counts = merged[code]
        if len(name_counts) == 1:
            partners[code] = list(name_counts.keys())[0]
        else:
            # 最頻値採用
            sorted_names = sorted(name_counts.items(), key=lambda x: x[1], reverse=True)
            adopted = sorted_names[0][0]
            partners[code] = adopted
            multi_name_details.append({
                "code": code,
                "candidates": sorted_names,
                "adopted": adopted,
            })

    return {
        "partners": partners,
        "dr_only": dr_only,
        "cr_only": cr_only,
        "both": both,
        "noise_count": noise_count,
        "multi_name_count": len(multi_name_details),
        "multi_name_details": multi_name_details,
    }


def _count_usage(code: str, groups: OrderedDict) -> tuple:
    """仕訳グループ内でのコードの使用件数 (借方, 貸方, 合計) を返す。"""
    dr_count = 0
    cr_count = 0
    for rows in groups.values():
        for r in rows:
            if r.get("dr_partner_code", "").strip() == code:
                dr_count += 1
            if r.get("cr_partner_code", "").strip() == code:
                cr_count += 1
    return dr_count, cr_count, dr_count + cr_count


def dedup_partners(
    partner_data: dict,
    strategy: str,
    all_groups: OrderedDict,
    custom_choices: dict = None,
) -> tuple:
    """
    同名異コード検出後、strategy に応じて統合 or 別名化 or スキップを実行する。

    strategy:
        "warn-only"     — 何もしない (従来動作)
        "interactive"   — 標準入力で 1 件ずつ対処を選択
        "merge-lowest"  — 最小コードに統合
        "merge-highest" — 最大コードに統合
        "merge-most-used" — 最多使用コードに統合
        "suffix"        — 使用回数少ない方に _2, _3 を付与
        "custom"        — custom_choices dict で各取引先の対処を指定

    custom_choices フォーマット (strategy="custom" 時):
        {
            "取引先名": {
                "action": "merge" | "rename" | "skip",
                "target_code": "742001"  # action="merge" の場合のみ
            },
            ...
        }

    戻り値:
        (deduped_partner_data, code_rewrite_map, name_override_map, audit_log)
        - deduped_partner_data: partner_data のコピーで partners を更新済み
        - code_rewrite_map: {古コード: 新コード} (merge 時のみ)
        - name_override_map: {コード: 表示名} (suffix 時および merge 時の名前確定)
        - audit_log: [(name, [old_codes], action_desc, result_desc), ...]
    """
    if strategy == "warn-only":
        return partner_data, {}, {}, []

    partners = dict(partner_data["partners"])  # code -> name のコピー

    # 同名異コードグループを構築
    name_to_codes: dict = defaultdict(list)
    for code, name in sorted(partners.items()):  # コード昇順で安定
        name_to_codes[name].append(code)

    same_name_groups = {
        name: codes
        for name, codes in name_to_codes.items()
        if len(codes) > 1
    }

    if not same_name_groups:
        return partner_data, {}, {}, []

    code_rewrite_map: dict = {}
    name_override_map: dict = {}
    audit_log: list = []
    skip_codes: set = set()

    sep = "=" * 70
    total = len(same_name_groups)

    for idx, (name, codes) in enumerate(sorted(same_name_groups.items()), start=1):
        # 使用回数を計算
        usage: dict = {}
        for code in codes:
            dr_cnt, cr_cnt, total_cnt = _count_usage(code, all_groups)
            usage[code] = {"dr": dr_cnt, "cr": cr_cnt, "total": total_cnt}

        old_codes = list(codes)

        if strategy == "interactive":
            # 対話プロンプト
            print(sep, file=sys.stderr)
            print(f"同名異コードの対処 ({idx}/{total})", file=sys.stderr)
            print(sep, file=sys.stderr)
            print(f"名前: {name}", file=sys.stderr)
            print("", file=sys.stderr)
            print("候補コード:", file=sys.stderr)
            for i, code in enumerate(codes, start=1):
                u = usage[code]
                print(f"  ({i}) {code}  (借方 {u['dr']} 回 / 貸方 {u['cr']} 回、仕訳合計 {u['total']} 件で使用)",
                      file=sys.stderr)
            print("", file=sys.stderr)
            print("対処方法を選んでください:", file=sys.stderr)
            for i, code in enumerate(codes, start=1):
                other_codes = [c for c in codes if c != code]
                others_str = " / ".join(other_codes)
                print(f"  ({i}) コード {code} に統合", file=sys.stderr)
                print(f"      → 仕訳 CSV の {others_str} を {code} に書き換え", file=sys.stderr)
                print(f"      → 取引先マスタは「{name} / {code}」1 行のみ", file=sys.stderr)
            print(f"  (3) 別名化" if len(codes) == 2 else f"  ({len(codes)+1}) 別名化", file=sys.stderr)
            suffix_opt_num = len(codes) + 1
            print(f"      → 取引先マスタは {len(codes)} 行: 使用回数多い方が元名、少ない方に _2 等を付与", file=sys.stderr)
            print(f"      → 仕訳 CSV のコードは変更しない (取引先名のみ書き換え)", file=sys.stderr)
            print(f"  (s) スキップ (取引先マスタから除外。手動で freee マスタ画面で対処)", file=sys.stderr)
            print("", file=sys.stderr)

            # 選択肢の番号を整理
            valid_merge_nums = list(range(1, len(codes) + 1))
            suffix_num = len(codes) + 1

            while True:
                try:
                    choice = input(f"選択 ({', '.join(str(n) for n in valid_merge_nums)}, {suffix_num}, s): ").strip().lower()
                except EOFError:
                    choice = "s"
                if choice in [str(n) for n in valid_merge_nums]:
                    chosen_idx = int(choice) - 1
                    adopted_code = codes[chosen_idx]
                    eliminated = [c for c in codes if c != adopted_code]
                    for old in eliminated:
                        code_rewrite_map[old] = adopted_code
                        if old in partners:
                            del partners[old]
                    name_override_map[adopted_code] = name
                    replaced_total = sum(usage[c]["total"] for c in eliminated)
                    action = f"({choice}) コード {adopted_code} に統合"
                    result = f"仕訳の {' / '.join(eliminated)} → {adopted_code} に置換 ({replaced_total} 件の仕訳が影響)"
                    audit_log.append((name, old_codes, action, result))
                    break
                elif choice == str(suffix_num):
                    _apply_suffix(name, codes, usage, partners, name_override_map)
                    action = f"({suffix_num}) 別名化"
                    result = f"マスタに {len(codes)} 行 ({' / '.join(name_override_map.get(c, name) for c in codes)})"
                    audit_log.append((name, old_codes, action, result))
                    break
                elif choice == "s":
                    for code in codes:
                        if code in partners:
                            del partners[code]
                    action = "(s) スキップ"
                    result = "マスタから除外、要手動対処"
                    audit_log.append((name, old_codes, action, result))
                    break
                else:
                    print("不正な選択です。もう一度入力してください: ", end="", flush=True, file=sys.stderr)

        elif strategy in ("merge-lowest", "merge-highest", "merge-most-used"):
            # 採用コード決定
            if strategy == "merge-lowest":
                adopted_code = min(codes)
            elif strategy == "merge-highest":
                adopted_code = max(codes)
            else:  # merge-most-used
                # 使用回数最多、同数はコード昇順
                adopted_code = max(codes, key=lambda c: (usage[c]["total"], -int(c) if c.isdigit() else 0))
                # 同数の場合コード昇順 (小さい方を採用)
                max_usage = usage[adopted_code]["total"]
                candidates = [c for c in codes if usage[c]["total"] == max_usage]
                adopted_code = min(candidates)

            eliminated = [c for c in codes if c != adopted_code]
            for old in eliminated:
                code_rewrite_map[old] = adopted_code
                if old in partners:
                    del partners[old]
            name_override_map[adopted_code] = name
            replaced_total = sum(usage[c]["total"] for c in eliminated)
            action = f"コード {adopted_code} に統合 ({strategy})"
            result = f"仕訳の {' / '.join(eliminated)} → {adopted_code} に置換 ({replaced_total} 件の仕訳が影響)"
            audit_log.append((name, old_codes, action, result))

        elif strategy == "suffix":
            _apply_suffix(name, codes, usage, partners, name_override_map)
            action = "別名化 (suffix)"
            result = f"マスタに {len(codes)} 行 ({' / '.join(name_override_map.get(c, name) for c in codes)})"
            audit_log.append((name, old_codes, action, result))

        elif strategy == "custom":
            choice_entry = (custom_choices or {}).get(name, {})
            c_action = choice_entry.get("action", "merge-most-used")  # デフォルト: merge-most-used

            if c_action == "merge":
                target_code = choice_entry.get("target_code", "")
                if target_code and target_code in codes:
                    adopted_code = target_code
                else:
                    # target_code 未指定 or 不正 → 最多使用コードにフォールバック
                    adopted_code = max(codes, key=lambda c: (usage[c]["total"], -int(c) if c.isdigit() else 0))
                    max_usage = usage[adopted_code]["total"]
                    candidates = [c for c in codes if usage[c]["total"] == max_usage]
                    adopted_code = min(candidates)
                eliminated = [c for c in codes if c != adopted_code]
                for old in eliminated:
                    code_rewrite_map[old] = adopted_code
                    if old in partners:
                        del partners[old]
                name_override_map[adopted_code] = name
                replaced_total = sum(usage[c]["total"] for c in eliminated)
                action = f"コード {adopted_code} に統合 (custom)"
                result = f"仕訳の {' / '.join(eliminated)} → {adopted_code} に置換 ({replaced_total} 件の仕訳が影響)"
                audit_log.append((name, old_codes, action, result))

            elif c_action == "rename":
                _apply_suffix(name, codes, usage, partners, name_override_map)
                action = "別名化 (custom/rename)"
                result = f"マスタに {len(codes)} 行 ({' / '.join(name_override_map.get(c, name) for c in codes)})"
                audit_log.append((name, old_codes, action, result))

            elif c_action == "skip":
                for code in codes:
                    if code in partners:
                        del partners[code]
                action = "スキップ (custom)"
                result = "マスタから除外"
                audit_log.append((name, old_codes, action, result))

            else:
                # 未知の action → merge-most-used 相当でフォールバック
                adopted_code = max(codes, key=lambda c: (usage[c]["total"], -int(c) if c.isdigit() else 0))
                max_usage = usage[adopted_code]["total"]
                candidates = [c for c in codes if usage[c]["total"] == max_usage]
                adopted_code = min(candidates)
                eliminated = [c for c in codes if c != adopted_code]
                for old in eliminated:
                    code_rewrite_map[old] = adopted_code
                    if old in partners:
                        del partners[old]
                name_override_map[adopted_code] = name
                replaced_total = sum(usage[c]["total"] for c in eliminated)
                action = f"コード {adopted_code} に統合 (custom/fallback)"
                result = f"仕訳の {' / '.join(eliminated)} → {adopted_code} に置換 ({replaced_total} 件の仕訳が影響)"
                audit_log.append((name, old_codes, action, result))

    # deduped_partner_data を構築
    deduped = dict(partner_data)
    deduped["partners"] = partners
    return deduped, code_rewrite_map, name_override_map, audit_log


def _apply_suffix(name: str, codes: list, usage: dict, partners: dict, name_override_map: dict):
    """suffix 戦略: 使用回数が多い方を 1 番目 (元名)、少ない方を _2, _3 に。"""
    # 使用回数降順、同数はコード昇順で並び替え
    sorted_codes = sorted(codes, key=lambda c: (-usage[c]["total"], c))
    for rank, code in enumerate(sorted_codes):
        if rank == 0:
            display_name = name
        else:
            display_name = f"{name}_{rank + 1}"
        name_override_map[code] = display_name
        partners[code] = display_name


def print_dedup_audit(strategy: str, audit_log: list, partners: dict):
    """同名異コード対処結果を stderr に出力する。"""
    sep = "=" * 70
    print(sep, file=sys.stderr)
    print(f"[同名異コード対処結果] dedup-strategy={strategy}", file=sys.stderr)
    print(sep, file=sys.stderr)
    print(f"処理した同名異コード: {len(audit_log)} 件", file=sys.stderr)
    print("", file=sys.stderr)
    for (name, old_codes, action, result) in audit_log:
        print(f"  {name}:", file=sys.stderr)
        print(f"    元: コード {' / '.join(old_codes)}", file=sys.stderr)
        print(f"    選択: {action}", file=sys.stderr)
        print(f"    結果: {result}", file=sys.stderr)
        print("", file=sys.stderr)
    print(f"最終取引先マスタ件数: {len(partners)} 件", file=sys.stderr)
    print(sep, file=sys.stderr)


def write_partners_csv(
    partner_data: dict,
    output_dir: str,
    partners_prefix: str,
):
    """
    取引先マスタ CSV を UTF-8 BOM 付きで書き出す。
    出力ファイル: {output_dir}/{partners_prefix}.csv
    列構成: freee 取引先インポートテンプレート 57 列
    """
    outpath = os.path.join(output_dir, f"{partners_prefix}.csv")

    partners = partner_data["partners"]
    # 取引先コード昇順ソート
    sorted_codes = sorted(partners.keys())

    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(outpath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(FREEE_PARTNER_HEADER)
            for code in sorted_codes:
                name = partners[code]
                row = [""] * len(FREEE_PARTNER_HEADER)
                row[0] = name    # 名前（通称）
                row[1] = code    # 取引先コード
                row[4] = name    # 正式名称（帳票出力時に使用される名称）
                writer.writerow(row)
    except (PermissionError, OSError) as e:
        print("=" * 70, file=sys.stderr)
        print("[エラー] 取引先マスタ CSV の書き出しに失敗しました", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print(f"出力先: {outpath}", file=sys.stderr)
        print(f"原因: {type(e).__name__}: {e}", file=sys.stderr)
        print("", file=sys.stderr)
        print("【ネクストアクション】", file=sys.stderr)
        print("  1. 出力先ディレクトリの書き込み権限を確認してください", file=sys.stderr)
        print("  2. ディスク残量を確認してください", file=sys.stderr)
        print("  3. 他のプロセスがファイルを開いていないか確認してください", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        sys.exit(1)

    return outpath, len(sorted_codes)


def print_partner_warnings(partner_data: dict):
    """
    同コード異名検出警告 + 同名異コード検出警告 + 取引先マスタ抽出件数サマリを stderr に出力する。
    2つの警告セクションは独立して評価・出力される (片方が出ても他方は抑制されない)。
    """
    sep = "=" * 70
    details = partner_data["multi_name_details"]

    # --- [要確認 取引先マスタ A] 同コード異名 ---
    if details:
        print(sep, file=sys.stderr)
        print("[要確認 取引先マスタ A] 同一コードに複数の名前が見つかった取引先", file=sys.stderr)
        print(sep, file=sys.stderr)
        print("以下の取引先は奉行原本で同一コードに複数の表記が含まれていました。", file=sys.stderr)
        print("最頻名を採用しましたが、freee 取り込み前に経理担当の確認推奨。", file=sys.stderr)
        print("", file=sys.stderr)
        for d in details:
            parts = " / ".join(
                f"'{name}' ({cnt} 件)" for name, cnt in d["candidates"]
            )
            print(f"  コード {d['code']}: {parts} → '{d['adopted']}' を採用", file=sys.stderr)
        print(f"\n  合計 {len(details)} 件", file=sys.stderr)
        print(sep, file=sys.stderr)
        print("", file=sys.stderr)

    # --- [要確認 取引先マスタ B] 同名異コード ---
    partners = partner_data["partners"]
    # 採用名 → コード集合 のマップを構築
    name_to_codes: dict = defaultdict(set)
    for code, name in partners.items():
        name_to_codes[name].add(code)

    same_name_diff_codes = {
        name: sorted(codes)
        for name, codes in name_to_codes.items()
        if len(codes) > 1
    }

    if same_name_diff_codes:
        print(sep, file=sys.stderr)
        print(
            f"[要確認 取引先マスタ B] 同一名に複数のコードが見つかった取引先 (合計 {len(same_name_diff_codes)} 件)",
            file=sys.stderr,
        )
        print(sep, file=sys.stderr)
        print("以下の取引先は同じ名前で複数のコードを持っています。", file=sys.stderr)
        print("freee に取り込むと同名の取引先が複数登録される状態になります。", file=sys.stderr)
        print("奉行原本側で名寄せを行うか、freee 取り込み後に手動で統合してください。", file=sys.stderr)
        print("", file=sys.stderr)
        for name, codes in sorted(same_name_diff_codes.items()):
            codes_str = " / ".join(codes)
            print(f"  名前 '{name}': コード {codes_str}", file=sys.stderr)
        print("", file=sys.stderr)
        print(f"  合計 {len(same_name_diff_codes)} 件", file=sys.stderr)
        print(sep, file=sys.stderr)
        print("", file=sys.stderr)

    # 抽出件数サマリ
    dr_only = partner_data["dr_only"]
    cr_only = partner_data["cr_only"]
    both = partner_data["both"]
    noise = partner_data["noise_count"]
    multi = partner_data["multi_name_count"]

    print(sep, file=sys.stderr)
    print(f"[取引先マスタ] 抽出件数: {len(partners)} 件", file=sys.stderr)
    print(f"  - 借方側出現: {len(dr_only | both)} 件", file=sys.stderr)
    print(f"  - 貸方側出現: {len(cr_only | both)} 件", file=sys.stderr)
    print(f"  - 両側出現:   {len(both)} 件", file=sys.stderr)
    print(f"  - ノイズ除去: {noise} 件 (000000/その他取引先/空白)", file=sys.stderr)
    print(f"  - 同コード異名検出: {multi} 件"
          + (" (上記 [要確認 取引先マスタ A] ログ参照)" if multi > 0 else ""),
          file=sys.stderr)
    print(f"  - 同名異コード検出: {len(same_name_diff_codes)} 件"
          + (" (上記 [要確認 取引先マスタ B] ログ参照)" if same_name_diff_codes else ""),
          file=sys.stderr)
    print(sep, file=sys.stderr)


# ---------------------------------------------------------------------------
# 奉行原本監査 (stderr 出力)
# ---------------------------------------------------------------------------

def audit_obc_source(input_files: list, date_from=None, date_to=None,
                     quiet: bool = False, encoding: str = "auto"):
    """
    奉行原本 CSV を直接走査して以下の2種を stderr に出力する。
      A: 伝票単位の借貸不一致 (借方本体金額, 貸方本体金額 カラムで集計)
      B: 課売上 + マイナス金額の返品疑い行 (借方/貸方税区分略称 カラムで判定)

    date_from/date_to の期間外伝票は除外 (変換対象と同一範囲のみ警告)。
    quiet=True の場合は何も出力しない。
    encoding: "auto" の場合は detect_encoding() で自動判定。
    """
    if quiet:
        return

    # --- 集計用データ構造 ---
    # A: slip_key -> (date_str, dr_total, cr_total)
    slip_dr: dict = defaultdict(int)
    slip_cr: dict = defaultdict(int)
    slip_date: dict = {}

    # B: 返品疑い行リスト [(slip_no, date_str, side, amount, summary), ...]
    minus_kaubai: list = []

    for fpath in input_files:
        try:
            if encoding == "auto":
                enc = detect_encoding(fpath)
            else:
                enc = encoding

            with open(fpath, encoding=enc, errors="replace") as f:
                reader = csv.reader(f)
                try:
                    header_row = next(reader)
                except StopIteration:
                    continue

                header_idx = build_header_index(header_row)

                for row in reader:
                    if not row:
                        continue

                    date_str = get_col(row, header_idx, OBC_COL_DATE).strip()
                    if not date_str:
                        continue

                    # 期間フィルタ (変換と同一ロジック)
                    if date_from or date_to:
                        try:
                            row_date = datetime.strptime(date_str, "%Y/%m/%d")
                            if date_from and row_date < date_from:
                                continue
                            if date_to and row_date > date_to:
                                continue
                        except ValueError:
                            pass

                    slip_no = get_col(row, header_idx, OBC_COL_SLIP_NO).strip()
                    slip_key = (date_str, slip_no)

                    # --- A: 借貸金額集計 ---
                    dr_val = get_col(row, header_idx, OBC_COL_DR_AMOUNT).strip()
                    cr_val = get_col(row, header_idx, OBC_COL_CR_AMOUNT).strip()
                    try:
                        slip_dr[slip_key] += int(dr_val) if dr_val else 0
                    except ValueError:
                        pass
                    try:
                        slip_cr[slip_key] += int(cr_val) if cr_val else 0
                    except ValueError:
                        pass
                    slip_date[slip_key] = date_str

                    # --- B: 課売上 + マイナス ---
                    summary = get_col(row, header_idx, OBC_COL_SUMMARY).strip()

                    # 借方側
                    dr_tax_label = get_col(row, header_idx, OBC_COL_DR_TAX_LABEL).strip()
                    if dr_tax_label == "課売上":
                        try:
                            dr_amount_int = int(dr_val) if dr_val else 0
                            if dr_amount_int < 0:
                                minus_kaubai.append((slip_no, date_str, "借方", dr_amount_int, summary))
                        except ValueError:
                            pass

                    # 貸方側
                    cr_tax_label = get_col(row, header_idx, OBC_COL_CR_TAX_LABEL).strip()
                    if cr_tax_label == "課売上":
                        try:
                            cr_amount_int = int(cr_val) if cr_val else 0
                            if cr_amount_int < 0:
                                minus_kaubai.append((slip_no, date_str, "貸方", cr_amount_int, summary))
                        except ValueError:
                            pass

        except OSError as e:
            print(f"WARN: 監査用ファイル読み込み失敗: {fpath}: {e}", file=sys.stderr)

    # --- 不一致伝票の抽出 ---
    mismatch_slips = []
    for key in sorted(slip_dr.keys()):
        dr = slip_dr[key]
        cr = slip_cr[key]
        if dr != cr:
            mismatch_slips.append((key[1], slip_date[key], dr, cr))  # (slip_no, date, dr, cr)

    # --- stderr 出力 ---
    sep = "=" * 70
    print(sep, file=sys.stderr)
    print("[要確認 1/2] 奉行原本由来の借貸不一致伝票 (経理担当の手動確認推奨)", file=sys.stderr)
    print(sep, file=sys.stderr)
    print("変換スクリプトは奉行原本のまま freee 形式へ展開しています。", file=sys.stderr)
    print("freee の各行は借貸一致するよう自動調整しますが、奉行原本の起票時点で", file=sys.stderr)
    print("借方合計 ≠ 貸方合計の伝票は freee インポート後も合計差が残る可能性があります。", file=sys.stderr)
    print("", file=sys.stderr)
    if mismatch_slips:
        for slip_no, date_str, dr, cr in mismatch_slips:
            diff = dr - cr
            sign = "+" if diff >= 0 else ""
            print(
                f"  No. {slip_no} (日付 {date_str}) "
                f"借方合計 {dr:>9,} / 貸方合計 {cr:>9,} / 差 {sign}{diff:,}",
                file=sys.stderr,
            )
        print(f"  合計 {len(mismatch_slips)} 件", file=sys.stderr)
    else:
        print("  (該当なし)", file=sys.stderr)

    print("", file=sys.stderr)
    print(sep, file=sys.stderr)
    print("[要確認 2/2] 課売上区分 + マイナス金額の返品疑い (課売返 への振替検討)", file=sys.stderr)
    print(sep, file=sys.stderr)
    print("返品/値引き仕訳は本来「課売返」区分が望ましいですが、", file=sys.stderr)
    print("以下の仕訳は「課売上」区分のままマイナス金額で起票されています。", file=sys.stderr)
    print("freee 取り込み後の消費税申告で「課税売上 − 返還等」(freee 税区分コード: 課税売返) への", file=sys.stderr)
    print("振替が必要な可能性があるため、経理担当の判断推奨。", file=sys.stderr)
    print("", file=sys.stderr)
    if minus_kaubai:
        for slip_no, date_str, side, amount, summary in minus_kaubai:
            summary_short = summary[:20] if summary else ""
            print(
                f"  No. {slip_no} ({date_str}) {side} 金額 {amount:,} 摘要: {summary_short}",
                file=sys.stderr,
            )
        print(f"  合計 {len(minus_kaubai)} 件", file=sys.stderr)
    else:
        print("  (該当なし)", file=sys.stderr)

    print("", file=sys.stderr)
    print(sep, file=sys.stderr)
    print("変換処理は正常完了しました。上記は freee エラー扱いではなく、", file=sys.stderr)
    print("経理担当の業務的確認を推奨する項目です。", file=sys.stderr)
    print(sep, file=sys.stderr)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="奉行 CSV → freee 33列 UTF-8 BOM CSV 変換")
    parser.add_argument("--input", nargs="+", required=True, help="奉行CSVファイルパス (複数可)")
    parser.add_argument("--output-dir", required=True, help="出力先ディレクトリ")
    parser.add_argument("--output-prefix", default="freee用_仕訳データ_obc変換", help="出力ファイル名プレフィックス")
    parser.add_argument("--rows-per-file", type=int, default=10000, help="ファイルあたり最大行数 (伝票境界をまたがない、最小 100)")
    parser.add_argument("--from", dest="date_from", default=None, help="開始日 YYYY/MM/DD (両端含む)")
    parser.add_argument("--to", dest="date_to", default=None, help="終了日 YYYY/MM/DD (両端含む)")
    parser.add_argument("--quiet-audit", action="store_true", help="監査ログ (stderr) を抑制する")
    parser.add_argument(
        "--encoding",
        choices=["auto", "cp932", "utf-8", "utf-8-sig"],
        default="auto",
        help="奉行 CSV のエンコーディング (デフォルト: 自動判定)",
    )
    parser.add_argument(
        "--output-partners",
        action="store_true",
        default=False,
        help="取引先マスタ CSV を追加出力する (デフォルト: OFF)",
    )
    parser.add_argument(
        "--partners-prefix",
        default="freee取引先マスタ",
        help="取引先マスタファイル名のプレフィックス (デフォルト: freee取引先マスタ)",
    )
    parser.add_argument(
        "--dedup-strategy",
        dest="dedup_strategy",
        choices=["warn-only", "interactive", "merge-lowest", "merge-highest",
                 "merge-most-used", "suffix", "custom"],
        default="warn-only",
        help=(
            "同名異コード検出時の対処戦略 (デフォルト: warn-only)。"
            "--output-partners 指定時のみ有効。"
            "warn-only=警告のみ / interactive=対話選択 / "
            "merge-lowest=最小コードに統合 / merge-highest=最大コードに統合 / "
            "merge-most-used=最多使用コードに統合 / suffix=別名化(_2,_3を付与) / "
            "custom=--dedup-custom-json で指定した JSON に従って各取引先を処理"
        ),
    )
    parser.add_argument(
        "--dedup-custom-json",
        dest="dedup_custom_json",
        default=None,
        help=(
            "--dedup-strategy=custom 時のカスタム選択 JSON ファイルパス。"
            'フォーマット: {"取引先名": {"action": "merge"|"rename"|"skip", "target_code": "742001"}, ...}'
        ),
    )
    parser.add_argument(
        "--detect-duplicates-only",
        dest="detect_duplicates_only",
        action="store_true",
        default=False,
        help=(
            "同名異コードを検出して JSON 形式で stdout に出力し、変換は行わない。"
            "GUI の対話フロー Phase 1 で使用。--output-partners は不要。"
        ),
    )
    parser.add_argument(
        "--known-balance-mismatches",
        dest="known_balance_mismatches",
        default="",
        help=(
            "奉行原本で既知の借貸不一致伝票番号をカンマ区切りで指定。"
            "例: --known-balance-mismatches \"002193,002820,003305\""
            " (デフォルト: 空 = すべて未知扱い)"
        ),
    )
    args = parser.parse_args()

    # 期間パース
    date_from = None
    date_to = None
    if args.date_from:
        date_from = datetime.strptime(args.date_from, "%Y/%m/%d")
    if args.date_to:
        date_to = datetime.strptime(args.date_to, "%Y/%m/%d")

    # 既知不一致伝票番号パース
    known_mismatch_slips: set = set()
    if args.known_balance_mismatches:
        known_mismatch_slips = {
            s.strip() for s in args.known_balance_mismatches.split(",") if s.strip()
        }

    # rows_per_file 最小値クランプ
    rows_per_file = max(100, args.rows_per_file)

    # 複数ファイルを順に読み込んでグループ統合
    all_groups = OrderedDict()
    total_input = 0
    for fpath in args.input:
        print(f"読み込み: {fpath}")
        groups = read_obc_csv(fpath, date_from, date_to, encoding=args.encoding)
        for key, rows in groups.items():
            if key not in all_groups:
                all_groups[key] = []
            all_groups[key].extend(rows)
            total_input += len(rows)
        print(f"  → {sum(len(v) for v in groups.values())} 行を読み込み (期間フィルタ後)")

    print(f"合計入力行数 (期間フィルタ後): {total_input}")
    print(f"伝票グループ数: {len(all_groups)}")

    # --detect-duplicates-only モード: 同名異コード検出して JSON 出力して終了
    if args.detect_duplicates_only:
        raw_partner_data = collect_partners(all_groups)
        partners = raw_partner_data["partners"]
        name_to_codes: dict = defaultdict(list)
        for code, name in sorted(partners.items()):
            name_to_codes[name].append(code)
        same_name_groups = {
            name: codes
            for name, codes in name_to_codes.items()
            if len(codes) > 1
        }
        duplicates = []
        for name, codes in sorted(same_name_groups.items()):
            codes_info = []
            for code in codes:
                dr_cnt, cr_cnt, total_cnt = _count_usage(code, all_groups)
                codes_info.append({
                    "code": code,
                    "usageCount": total_cnt,
                    "debitUsage": dr_cnt,
                    "creditUsage": cr_cnt,
                })
            duplicates.append({"name": name, "codes": codes_info})
        print(json.dumps({"success": True, "duplicates": duplicates}, ensure_ascii=False, indent=2))
        return

    # 取引先 dedup 処理 (--output-partners かつ warn-only 以外のとき仕訳 CSV 書き出し前に実行)
    code_rewrite_map: dict = {}
    name_override_map: dict = {}
    dedup_audit_log: list = []
    deduped_partner_data = None

    if args.output_partners:
        raw_partner_data = collect_partners(all_groups)
        # custom 戦略の場合は JSON 読み込み
        custom_choices = None
        if args.dedup_strategy == "custom" and args.dedup_custom_json:
            try:
                with open(args.dedup_custom_json, "r", encoding="utf-8") as _f:
                    custom_choices = json.load(_f)
                print(f"[dedup] custom 選択 JSON 読み込み: {args.dedup_custom_json} ({len(custom_choices)} 件)")
            except Exception as e:
                print(f"[dedup] custom JSON 読み込み失敗: {e} — merge-most-used にフォールバック", file=sys.stderr)
                args.dedup_strategy = "merge-most-used"
        deduped_partner_data, code_rewrite_map, name_override_map, dedup_audit_log = dedup_partners(
            raw_partner_data,
            args.dedup_strategy,
            all_groups,
            custom_choices=custom_choices,
        )

    # 全グループ変換 (process_groups は (all_rows, slip_sizes) を返す)
    # dedup 後の code_rewrite_map / name_override_map を仕訳行に反映する
    all_rows, slip_sizes = process_groups(
        all_groups,
        code_rewrite_map=code_rewrite_map if code_rewrite_map else None,
        name_override_map=name_override_map if name_override_map else None,
    )
    print(f"出力行数: {len(all_rows)}")

    # 伝票単位借貸整合性チェック (ファイル書き出し前)
    # 未知の不一致は check_slip_balance 内で _errors.add_balance() に追加される。
    # 終了コード判定は後段の _errors.has_critical() で一元管理するため、戻り値は受けない。
    print()
    check_slip_balance(all_rows, label=args.output_prefix,
                       known_mismatch_slips=known_mismatch_slips)
    print()

    # 出力 (balance NG でも出力はスキップせず、サマリで警告)
    written = write_output(
        all_rows,
        slip_sizes,
        args.output_dir,
        args.output_prefix,
        rows_per_file,
    )

    print(f"\n完了: {len(written)} ファイル出力")
    for path, count in written:
        print(f"  {path}  ({count} データ行 + 1 ヘッダー行)")

    # 取引先マスタ CSV 出力 (--output-partners 指定時)
    if args.output_partners:
        # dedup 監査ログ出力 (warn-only 以外で処理あり)
        if args.dedup_strategy != "warn-only" and dedup_audit_log:
            print_dedup_audit(args.dedup_strategy, dedup_audit_log, deduped_partner_data["partners"])
        partner_path, partner_count = write_partners_csv(
            deduped_partner_data,
            args.output_dir,
            args.partners_prefix,
        )
        print(f"\n取引先マスタ: {partner_path}  ({partner_count} 件)")
        print_partner_warnings(deduped_partner_data)

    # 奉行原本監査ログ (stderr)
    audit_obc_source(args.input, date_from, date_to,
                     quiet=args.quiet_audit, encoding=args.encoding)

    # エラーレポート (stderr)
    _errors.print_report(
        total_input=total_input,
        total_output=len(all_rows),
    )

    # 終了コード: CAT_BALANCE_UNKNOWN (変換バグ) があれば 1
    if _errors.has_critical():
        sys.exit(1)


if __name__ == "__main__":
    main()
