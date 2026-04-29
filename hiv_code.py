# -*- coding: utf-8 -*-
"""
HIV 匿名諮詢代碼批次取號工具
hiva.cdc.gov.tw 自動化
衛生所外展用 — 協助民眾批次產生諮詢代碼

v1.0.0
"""
import os
import sys
import time
import random
import threading
import queue
import json
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

VERSION = "1.0.35"
DEBUG = True  # DEBUG 版：失敗時自動存 HTML 快照、log 詳細

# ── v1.0.28：lazy import selenium → 啟動加速 ──
# 不在 import 階段載入 selenium（拖慢 EXE 冷啟動約 1-2 秒）
# 真正用到時才在 HivaWorker._import_selenium() 裡載入
# PyInstaller 端透過 spec 的 hidden_imports 列舉確保打包完整

# ── 常數 ──────────────────────────────────────────
URL_HOME = "https://hiva.cdc.gov.tw/Default.aspx"
URL_QUESTIONNAIRE = "https://hiva.cdc.gov.tw/Questionnaire.aspx"

CITIES = [
    "台北市", "新北市", "桃園市", "台中市", "台南市", "高雄市",
    "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣",
    "屏東縣", "宜蘭縣", "花蓮縣", "台東縣",
    "澎湖縣", "金門縣", "連江縣",
]
GENDERS = ["男", "女", "跨性別", "其他"]
NATIONS = ["本國籍", "外國籍"]
EDUS    = ["不識字", "國中以下", "高中職", "專科或大學", "研究所(含)以上"]
ORIENTS = ["同性", "雙性", "異性"]
TESTING = ["否", "從未做過"]

OUTPUT_DIR = r"D:\Backup\Desktop\CODE\number"   # v1.0.15：可由 UI 動態變更（settings.json 持久化）
DEFAULT_OUTPUT_DIR = OUTPUT_DIR

def _settings_path():
    """settings.json 永遠放在 EXE 旁邊（地端習慣），不跟著 output_dir 移動"""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "settings.json")

SETTINGS_FILE = _settings_path()

def set_output_dir(new_path):
    """更新全域 OUTPUT_DIR；自動建立資料夾結構（含 old / log / debug）"""
    global OUTPUT_DIR
    OUTPUT_DIR = new_path
    ensure_outdir()

# ── v1.0.18 主題配色 ──
THEMES = {
    "Ocean (海洋藍)": {
        "bg": "#eef5fa", "panel": "#ffffff", "fg": "#1a2a3a",
        "accent": "#1565c0", "accent_hover": "#0d47a1",
        "success": "#2e7d32", "warn": "#ef6c00", "error": "#c62828",
        "log_bg": "#0e1a26", "log_fg": "#dce6f0",
        "log_success": "#66bb6a", "log_warn": "#ffb74d", "log_error": "#ef5350",
    },
    "Sunset (夕陽橘)": {
        "bg": "#fff3e0", "panel": "#fffbf5", "fg": "#3e2723",
        "accent": "#e65100", "accent_hover": "#bf360c",
        "success": "#558b2f", "warn": "#ef6c00", "error": "#c62828",
        "log_bg": "#1f1612", "log_fg": "#ffe0b2",
        "log_success": "#9ccc65", "log_warn": "#ffb74d", "log_error": "#ef5350",
    },
    "Forest (森林綠)": {
        "bg": "#e8f5e9", "panel": "#f7fbf7", "fg": "#1b3a1e",
        "accent": "#2e7d32", "accent_hover": "#1b5e20",
        "success": "#43a047", "warn": "#f57c00", "error": "#d32f2f",
        "log_bg": "#0e1a12", "log_fg": "#c8e6c9",
        "log_success": "#81c784", "log_warn": "#ffb74d", "log_error": "#ef5350",
    },
    "Dark (深色)": {
        "bg": "#263238", "panel": "#37474f", "fg": "#eceff1",
        "accent": "#26c6da", "accent_hover": "#0097a7",
        "success": "#66bb6a", "warn": "#ffa726", "error": "#ef5350",
        "log_bg": "#1c2025", "log_fg": "#cfd8dc",
        "log_success": "#81c784", "log_warn": "#ffb74d", "log_error": "#ef5350",
    },
}
DEFAULT_THEME = "Ocean (海洋藍)"

# v1.0.21 完整模式 schema（41 欄）
# 每筆 record 在 xlsx 裡的欄位順序、key、label、預設、可選值
COMPLETE_FIELDS = [
    # P1
    ("q1_sex",         "P1Q1 過性行為",           "否",     ["是", "否"]),
    ("q2_condom",      "P1Q2 全程保險套",         "沒有發生",["是", "否", "沒有發生"]),
    ("q3_regular",     "P1Q3 跟固定性伴侶",       "沒有發生",["是", "否", "沒有發生"]),
    ("q4_alcohol",     "P1Q4 用酒",               "沒有發生",["是", "否", "沒有發生"]),
    ("q5_drug",        "P1Q5 用藥",               "沒有發生",["是", "否", "沒有發生"]),
    # P2
    ("q6_std",         "P2Q6 感染性病",           "否",     ["是", "否"]),
    ("q6_hiv",         "P2Q6.1 HIV",              "否",     ["是", "否"]),
    ("q6_hiv_reason",  "P2Q6.1 HIV 想再篩原因",   "",       None),
    ("q6_warts",       "P2Q6.2 菜花",             "否",     ["是", "否"]),
    ("q6_syphilis",    "P2Q6.3 梅毒",             "否",     ["是", "否"]),
    ("q6_gonorrhea",   "P2Q6.4 淋病",             "否",     ["是", "否"]),
    ("q6_chlamydia",   "P2Q6.5 披衣菌",           "否",     ["是", "否"]),
    ("q6_herpes",      "P2Q6.6 疱疹",             "否",     ["是", "否"]),
    ("q6_hepA",        "P2Q6.7 A肝",              "否",     ["是", "否"]),
    ("q6_hepC",        "P2Q6.8 C肝",              "否",     ["是", "否"]),
    ("q6_other",       "P2Q6.9 其他性病",         "",       None),
    # P3
    ("q7_drug_use",    "P3Q7 成癮藥物",           "否",     ["是", "否"]),
    ("q7_amph",        "P3Q7.1 安非他命",         "否",     ["是", "否"]),
    ("q7_amph_method", "P3Q7.1 安非他命使用方式", "",       None),  # 多選用逗號分隔：吸入,注射,口服
    ("q7_ghb",         "P3Q7.2 G水",              "否",     ["是", "否"]),
    ("q7_mdma",        "P3Q7.3 搖頭丸",           "否",     ["是", "否"]),
    ("q7_ketamine",    "P3Q7.4 K他命",            "否",     ["是", "否"]),
    ("q7_rush",        "P3Q7.5 RUSH",             "否",     ["是", "否"]),
    ("q7_meph",        "P3Q7.6 喵喵",             "否",     ["是", "否"]),
    ("q7_heroin",      "P3Q7.7 海洛因",           "否",     ["是", "否"]),
    ("q7_marijuana",   "P3Q7.8 大麻",             "否",     ["是", "否"]),
    ("q7_other",       "P3Q7.9 其他藥物",         "",       None),
    ("q7_status",      "P3Q7.10 目前使用狀態",    "",       ["還在使用", "已停用"]),
    # P4
    ("q8a_online",     "P4Q8a 網路認識",          "否",     ["是", "否"]),
    ("q8b_venue",      "P4Q8b 娛樂場所認識",      "否",     ["是", "否"]),
    ("q8c_sex_worker", "P4Q8c 性交易服務者",      "否",     ["是", "否"]),
    ("q8d_sex_consumer","P4Q8d 性交易消費者",     "否",     ["是", "否"]),
    # P5
    ("q9_partner_hiv", "P5Q9 固定伴侶HIV",        "否",     ["是", "否", "不確定", "目前沒有固定性伴侶"]),
    ("q10_pep_used",   "P5Q10 PEP使用過",         "否",     ["是", "否"]),
    ("q11_pep_want",   "P5Q11 PEP想服用",         "否",     ["是", "否"]),
    ("q12_prep_heard", "P5Q12 聽過PrEP",          "否",     ["是", "否"]),
    ("q13_prep_want",  "P5Q13 PrEP想服用",        "否",     ["是", "否"]),
    # P6
    ("gender",         "P6 性別",                 "男",     GENDERS),
    ("nation",         "P6 國籍",                 "本國籍",  NATIONS),
    ("year",           "P6 出生年",               1990,     None),
    ("res18",          "P6 18歲前居住地",         "台南市",  CITIES),
    ("resCur",         "P6 現居住地",             "台南市",  CITIES),
    ("orient",         "P6 性傾向",               "異性",   ORIENTS),
    ("edu",            "P6 教育程度",             "高中職",  EDUS),
    # P7
    ("testing_habit",  "P7Q1 篩檢習慣",           "否",     ["是", "否", "從未做過"]),
    ("phone",          "P7 手機號碼(選填)",       "",       None),
    ("email",          "P7 E-mail(選填)",         "",       None),
    ("other_contact",  "P7 其他聯絡(選填)",       "",       None),
]
COMPLETE_KEYS = [k for k, _, _, _ in COMPLETE_FIELDS]
COMPLETE_FIELD_ALLOWED = {k: vals for k, _, _, vals in COMPLETE_FIELDS if vals}

# v1.0.29：Excel 輸出用的乾淨欄位名（無 P1Q1 prefix），對應 COMPLETE_FIELDS 的 key
OUTPUT_LABELS = {
    "q1_sex": "Q1 過性行為", "q2_condom": "Q2 全程保險套",
    "q3_regular": "Q3 跟固定性伴侶", "q4_alcohol": "Q4 用酒", "q5_drug": "Q5 用藥",
    "q6_std": "Q6 感染性病", "q6_hiv": "Q6.1 HIV", "q6_hiv_reason": "Q6.1 HIV原因",
    "q6_warts": "Q6.2 菜花", "q6_syphilis": "Q6.3 梅毒",
    "q6_gonorrhea": "Q6.4 淋病", "q6_chlamydia": "Q6.5 披衣菌",
    "q6_herpes": "Q6.6 疱疹", "q6_hepA": "Q6.7 A肝",
    "q6_hepC": "Q6.8 C肝", "q6_other": "Q6.9 其他性病",
    "q7_drug_use": "Q7 成癮藥物", "q7_amph": "Q7.1 安非他命",
    "q7_amph_method": "Q7.1 使用方式", "q7_ghb": "Q7.2 G水",
    "q7_mdma": "Q7.3 搖頭丸", "q7_ketamine": "Q7.4 K他命",
    "q7_rush": "Q7.5 RUSH", "q7_meph": "Q7.6 喵喵",
    "q7_heroin": "Q7.7 海洛因", "q7_marijuana": "Q7.8 大麻",
    "q7_other": "Q7.9 其他藥物", "q7_status": "Q7 目前使用狀態",
    "q8a_online": "Q8a 網路認識", "q8b_venue": "Q8b 娛樂場所認識",
    "q8c_sex_worker": "Q8c 性交易服務者", "q8d_sex_consumer": "Q8d 性交易消費者",
    "q9_partner_hiv": "Q9 固定伴侶HIV", "q10_pep_used": "Q10 PEP使用過",
    "q11_pep_want": "Q11 PEP想服用", "q12_prep_heard": "Q12 聽過PrEP",
    "q13_prep_want": "Q13 PrEP想服用",
    "gender": "性別", "nation": "國籍", "year": "出生年",
    "res18": "18歲前居住地", "resCur": "現居住地",
    "orient": "性傾向", "edu": "教育程度",
    "testing_habit": "篩檢習慣", "phone": "手機", "email": "E-mail",
    "other_contact": "其他聯絡",
}
# v1.0.35：摘要欄（B方案）
OUTPUT_SUMMARY_LABELS = ["篩檢路徑", "風險摘要"]
# 輸出欄位順序：基本元資料 + 摘要欄 + COMPLETE_FIELDS 順序
OUTPUT_HEADERS = (["#", "諮詢代碼", "取得時間"] + OUTPUT_SUMMARY_LABELS
                  + [OUTPUT_LABELS.get(k, k) for k in COMPLETE_KEYS])


def _classify_record(r):
    """v1.0.35：根據答案判斷篩檢路徑 + 風險摘要"""
    is_yes_q1 = r.get("q1_sex") == "是"
    is_yes_q6 = r.get("q6_std") == "是"
    is_yes_q7 = r.get("q7_drug_use") == "是"
    # 高風險指標
    risk_high = (is_yes_q6 or is_yes_q7
                 or r.get("q7_amph") == "是" or r.get("q7_heroin") == "是"
                 or r.get("q9_partner_hiv") == "是"
                 or r.get("q8a_online") == "是" or r.get("q8b_venue") == "是"
                 or r.get("q8c_sex_worker") == "是" or r.get("q8d_sex_consumer") == "是")
    # 路徑判定
    if is_yes_q6:
        path = "感染史"
    elif is_yes_q7:
        path = "藥物史"
    elif is_yes_q1:
        path = "性行為"
    else:
        path = "簡單"
    # 風險摘要
    if risk_high:
        risk = "高"
    elif is_yes_q1:
        risk = "中"
    else:
        risk = "低"
    return path, risk

# 各頁的 radio group 對應 profile keys（依 DOM 出現順序）
PAGE1_KEYS = ["q1_sex", "q2_condom", "q3_regular", "q4_alcohol", "q5_drug"]
PAGE2_BASE_KEY  = "q6_std"
PAGE2_SUB_KEYS  = ["q6_hiv", "q6_warts", "q6_syphilis", "q6_gonorrhea",
                   "q6_chlamydia", "q6_herpes", "q6_hepA", "q6_hepC"]
PAGE3_BASE_KEY  = "q7_drug_use"
PAGE3_SUB_KEYS  = ["q7_amph", "q7_ghb", "q7_mdma", "q7_ketamine", "q7_rush",
                   "q7_meph", "q7_heroin", "q7_marijuana", "q7_status"]
PAGE4_KEYS = ["q8a_online", "q8b_venue", "q8c_sex_worker", "q8d_sex_consumer"]
PAGE5_KEYS = ["q9_partner_hiv", "q10_pep_used", "q11_pep_want", "q12_prep_heard", "q13_prep_want"]
PAGE7_BASE_KEY = "testing_habit"
PAGE7_TEXT_KEYS = ["phone", "email", "other_contact"]


def make_sample_xlsx(path):
    """v1.0.31：仿衛福部官方雙層樣式 — 標題列深藍底白字 + 描述列淺黃底深字 + 樣本列灰底"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.comments import Comment
    wb = Workbook()
    ws = wb.active
    ws.title = "匿名HIV取號_範例檔"

    headers = [label for _, label, _, _ in COMPLETE_FIELDS]
    # 描述行（每個欄位的選項提示）
    def _opt_str(allowed, dv):
        if allowed:
            return "/".join(allowed)
        return "（自由文字）"
    desc_row = [_opt_str(allowed, dv) for _, _, dv, allowed in COMPLETE_FIELDS]
    default_row = [dv for _, _, dv, _ in COMPLETE_FIELDS]
    sample = {
        "gender": "女", "year": 1990, "res18": "台南市", "resCur": "台南市",
        "orient": "異性", "edu": "高中職", "testing_habit": "否",
    }
    sample_row = [sample.get(k, dv) for k, _, dv, _ in COMPLETE_FIELDS]

    # Layout：
    #   Row 1 — 標題列（深藍底白字粗體）
    #   Row 2 — 選項提示（淺黃底）
    #   Row 3 — 預設值範本（灰底+斜體，匯入會自動忽略）
    #   Row 4 — 實際資料起點（白色）
    ws.append(headers)
    ws.append(desc_row)
    ws.append(default_row)
    ws.append(sample_row)

    # 樣式
    thin = Side(border_style="thin", color="546E7A")
    border = Border(top=thin, bottom=thin, left=thin, right=thin)

    # Row 1 標題：深藍底白字
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", size=11, name="微軟正黑體")
        c.fill = PatternFill("solid", fgColor="1A237E")  # 深藍
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
    # Row 2 描述：淺黃底深字
    for c in ws[2]:
        c.font = Font(color="6D4C41", size=10, name="微軟正黑體")
        c.fill = PatternFill("solid", fgColor="FFF59D")  # 淺黃
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
    # Row 3 預設值：灰底+斜體（提示：此列匯入會被自動忽略）
    for c in ws[3]:
        c.font = Font(italic=True, color="78909C", size=9, name="微軟正黑體")
        c.fill = PatternFill("solid", fgColor="ECEFF1")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border
    # Row 4 範例資料（白底正常）
    for c in ws[4]:
        c.font = Font(color="263238", size=10, name="微軟正黑體")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border

    ws["A1"].comment = Comment(
        "規則說明：\n"
        " ‧ 第 1 列：欄位名稱（請勿修改）\n"
        " ‧ 第 2 列：選項提示（請勿修改）\n"
        " ‧ 第 3 列：預設值範本（匯入會自動忽略）\n"
        " ‧ 第 4 列起：填寫實際資料\n\n"
        "提示：每個欄位的儲存格有下拉清單可直接選取，不必手動輸入。",
        "HIV 取號工具"
    )

    # 欄寬：依文字長度估
    for col_idx, label in enumerate(headers, start=1):
        col_letter = ws.cell(1, col_idx).column_letter
        ws.column_dimensions[col_letter].width = max(16, min(28, len(str(label)) + 4))
    ws.row_dimensions[1].height = 32
    ws.row_dimensions[2].height = 28
    ws.row_dimensions[3].height = 18

    # 下拉資料驗證（從第 4 列開始套用）
    for col_idx, (key, label, dv, allowed) in enumerate(COMPLETE_FIELDS, start=1):
        if not allowed:
            continue
        opts = ",".join([str(v) for v in allowed])
        if len(opts) > 250:
            continue
        col_letter = ws.cell(1, col_idx).column_letter
        dv_obj = DataValidation(type="list", formula1=f'"{opts}"', allow_blank=True,
                                showErrorMessage=True,
                                errorTitle="無效選項",
                                error=f"請從下拉選擇：{opts[:60]}...")
        dv_obj.add(f"{col_letter}4:{col_letter}500")
        ws.add_data_validation(dv_obj)
    # 22 縣市改用 named range
    ws_cities = wb.create_sheet("__cities__")
    ws_cities.sheet_state = "hidden"
    for i, c in enumerate(CITIES, start=1):
        ws_cities.cell(i, 1, c)
    cities_range = f"__cities__!$A$1:$A${len(CITIES)}"
    for col_idx, (key, label, dv, allowed) in enumerate(COMPLETE_FIELDS, start=1):
        if allowed is CITIES or (allowed and len(",".join(allowed)) > 250):
            col_letter = ws.cell(1, col_idx).column_letter
            dv_obj = DataValidation(type="list", formula1=f"={cities_range}", allow_blank=True)
            dv_obj.add(f"{col_letter}4:{col_letter}500")
            ws.add_data_validation(dv_obj)

    ws.freeze_panes = "A4"  # 凍結到第 4 列起，前 3 列固定
    wb.save(path)


REQUIRED_KEYS_FOR_WARN = {
    # 一定要填（沒填要警示）
    "gender", "nation", "year", "res18", "resCur", "orient", "edu", "testing_habit",
}

def import_xlsx_profiles(path):
    """v1.0.31/33：讀取 xlsx
       回 (profiles, warnings_dict)
       warnings_dict = {"blank": [...每列未填的欄位...], "invalid": [...無效值...]}"""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], {"blank": [], "invalid": [], "fatal": "Excel 是空的"}
    header_row = rows[0]
    header_to_key = {label: k for k, label, _, _ in COMPLETE_FIELDS}
    col_keys = []
    for h in header_row:
        h_clean = (str(h or "")).strip()
        col_keys.append(header_to_key.get(h_clean))
    profiles = []
    blanks_by_row = []   # [(row_idx, [field_labels])]
    invalids = []        # [(row_idx, field_label, bad_value, default)]
    # 自動跳過輔助列
    start = 1
    if len(rows) >= 2:
        row2_text = " ".join(str(c or "") for c in rows[1])
        if "/" in row2_text or "自由文字" in row2_text:
            start = max(start, 2)
    if len(rows) >= 3:
        defaults = [dv for _, _, dv, _ in COMPLETE_FIELDS]
        row3 = list(rows[2])
        check_n = min(len(row3), len(defaults))
        if check_n > 0:
            match = sum(1 for i in range(check_n) if str(row3[i] or "") == str(defaults[i] or ""))
            if match >= check_n * 0.6:
                start = max(start, 3)
    for ridx, row in enumerate(rows[start:], start=start + 1):
        if not row or all(c is None or c == "" for c in row):
            continue
        prof_raw = {}
        for i, val in enumerate(row):
            if i >= len(col_keys): break
            k = col_keys[i]
            if k is None: continue
            prof_raw[k] = val
        prof = {}
        row_blanks = []
        for k, _, dv, allowed in COMPLETE_FIELDS:
            actual = prof_raw.get(k)
            is_blank = (actual is None or str(actual).strip() == "")
            if is_blank:
                # 只警示「應填但沒填」的（required 清單），其他靜默套用預設
                if k in REQUIRED_KEYS_FOR_WARN:
                    label = OUTPUT_LABELS.get(k, k)
                    row_blanks.append(label)
                prof[k] = dv
            elif allowed:
                v = str(actual).strip()
                if v not in allowed:
                    label = OUTPUT_LABELS.get(k, k)
                    invalids.append((ridx, label, v, dv))
                    prof[k] = dv
                else:
                    prof[k] = v
            else:
                prof[k] = actual
        if row_blanks:
            blanks_by_row.append((ridx, row_blanks))
        try: prof["year"] = int(prof.get("year") or 1990)
        except Exception: prof["year"] = 1990
        profiles.append(prof)
    return profiles, {"blank": blanks_by_row, "invalid": invalids}


class CanvasProgressBar(tk.Canvas):
    """v1.0.19：自訂進度條 — 高度可調 + 中央顯示 % 文字
       注意：`self._w` 與 `self._h` 是 Tk widget 內部屬性（Tcl 名稱），不可覆蓋！
       這裡改用 `self._cw` / `self._ch`。"""
    def __init__(self, parent, width=600, height=34, bg="#e0e0e0", fg="#1565c0", **kw):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, **kw)
        self._cw, self._ch = width, height
        self._bg, self._fg = bg, fg
        self.bar = self.create_rectangle(0, 0, 0, height, fill=fg, width=0)
        self.txt = self.create_text(width // 2, height // 2,
                                     text="0.0%", fill="#37474f",
                                     font=("微軟正黑體", 12, "bold"))
        self.value = 0; self.maximum = 100
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        self._cw = event.width
        self.coords(self.txt, self._cw // 2, self._ch // 2)
        self._refresh()

    def configure_value(self, value, maximum=None):
        if maximum is not None:
            self.maximum = max(1, maximum)
        self.value = max(0, min(value, self.maximum))
        self._refresh()

    def _refresh(self):
        pct = (self.value / self.maximum) if self.maximum else 0
        w = int(self._cw * pct)
        self.coords(self.bar, 0, 0, w, self._ch)
        self.itemconfigure(self.txt, text=f"{pct*100:.1f}%")
        # 文字顏色：bar 蓋過中央時用白字，否則用深色
        self.itemconfigure(self.txt, fill="white" if pct >= 0.45 else "#263238")

    def configure_theme(self, bg, fg):
        self._bg, self._fg = bg, fg
        self.configure(bg=bg)
        self.itemconfigure(self.bar, fill=fg)

# ── 工具函式 ──────────────────────────────────────
def ensure_outdir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "old"), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "log"), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, "debug"), exist_ok=True)

# ── 全域 log file（每次啟動 GUI 開新檔） ──
_LOG_FP = None
def init_logfile():
    global _LOG_FP
    ensure_outdir()
    fp = os.path.join(OUTPUT_DIR, "log",
        f"log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    try:
        _LOG_FP = open(fp, "w", encoding="utf-8", buffering=1)  # line-buffered
    except Exception:
        _LOG_FP = None
    return fp

def write_log_line(line):
    if _LOG_FP:
        try:
            _LOG_FP.write(line + "\n")
        except Exception:
            pass

def debug_dir():
    p = os.path.join(OUTPUT_DIR, "debug")
    os.makedirs(p, exist_ok=True)
    return p

def archive_old_outputs(log_fn=None):
    """把 number/ 下「非今天」的 *.xlsx / *.csv 搬到 number/old/。
       v1.0.17：保留今天的檔案以便同日合併（merge 邏輯在 _incremental_save_excel）"""
    old_dir = os.path.join(OUTPUT_DIR, "old")
    os.makedirs(old_dir, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y%m%d")
    moved = 0
    try:
        for fn in os.listdir(OUTPUT_DIR):
            full = os.path.join(OUTPUT_DIR, fn)
            if not os.path.isfile(full):
                continue
            if not (fn.lower().endswith(".xlsx") or fn.lower().endswith(".csv")):
                continue
            # v1.0.17：codes_<TODAY>_*.xlsx 留下，給同日合併用
            if fn.startswith(f"codes_{today}_"):
                continue
            try:
                import shutil
                target = os.path.join(old_dir, fn)
                if os.path.exists(target):
                    stem, ext = os.path.splitext(fn)
                    target = os.path.join(old_dir,
                        f"{stem}_{datetime.datetime.now().strftime('%H%M%S')}{ext}")
                shutil.move(full, target)
                moved += 1
                if log_fn: log_fn(f"  → 舊檔搬移：{fn}")
            except Exception as e:
                if log_fn: log_fn(f"  ⚠ 搬移失敗 {fn}：{e}")
    except FileNotFoundError:
        pass
    if moved and log_fn:
        log_fn(f"📦 已將 {moved} 個舊檔搬至 number\\old\\")
    return moved

def save_debug_snapshot(driver, tag):
    """DEBUG 版：失敗時把當下頁面 HTML + screenshot 存到 number/debug/"""
    if not DEBUG or not driver:
        return
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        base = os.path.join(debug_dir(), f"{ts}_{tag}")
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        try: driver.save_screenshot(base + ".png")
        except Exception: pass
    except Exception:
        pass

def weighted_pick(items, weights):
    """依比例權重隨機抽一個項目（weights 不必正規化）"""
    if not items:
        return None
    total = sum(max(0, w) for w in weights)
    if total <= 0:
        return random.choice(items)
    r = random.uniform(0, total)
    acc = 0
    for it, w in zip(items, weights):
        acc += max(0, w)
        if r <= acc:
            return it
    return items[-1]

def jitter(a=0.6, b=1.6):
    """模擬人類延遲（純函式版本，作為 fallback）"""
    time.sleep(random.uniform(a, b))


class DelayConfig:
    """從 GUI 拿到的延遲設定，由 HivaWorker 引用"""
    def __init__(self, action_lo=0.3, action_hi=0.7,
                 page_lo=0.8, page_hi=1.5,
                 between_lo=1.5, between_hi=3.0,
                 wait_timeout=30):
        self.action_lo  = float(action_lo)
        self.action_hi  = float(action_hi)
        self.page_lo    = float(page_lo)
        self.page_hi    = float(page_hi)
        self.between_lo = float(between_lo)
        self.between_hi = float(between_hi)
        self.wait_timeout = int(wait_timeout)  # selenium 等待逾時（多人連線會慢，預設拉長）

    def action(self):  time.sleep(random.uniform(self.action_lo, self.action_hi))
    def page(self):    time.sleep(random.uniform(self.page_lo,   self.page_hi))
    def between(self): time.sleep(random.uniform(self.between_lo, self.between_hi))


# ── Selenium：取代碼工人 ──────────────────────────
class HivaWorker:
    def __init__(self, log_fn, status_fn, code_fn, stop_evt, delay_cfg=None):
        self.log = log_fn
        self.status = status_fn
        self.on_code = code_fn
        self.stop_evt = stop_evt
        self.driver = None
        self.dly = delay_cfg or DelayConfig()

    def _import_selenium(self):
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
        from selenium.webdriver.edge.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait, Select
        from selenium.webdriver.support import expected_conditions as EC
        return webdriver, Options, Service, By, WebDriverWait, Select, EC

    def start_browser(self):
        webdriver, Options, Service, By, WebDriverWait, Select, EC = self._import_selenium()
        opts = Options()
        # ── 啟動參數：移除自動化痕跡 ──
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation", "load-extension"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument("--start-maximized")
        opts.add_argument("--lang=zh-TW")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-features=IsolateOrigins,site-per-process")
        opts.add_argument("--disable-site-isolation-trials")
        # 真實 UA（與 Edge 130 相符）
        opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
        # prefs：關掉密碼儲存提示等
        opts.add_experimental_option("prefs", {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "profile.default_content_setting_values.notifications": 2,
        })
        try:
            self.driver = webdriver.Edge(options=opts)
        except Exception as e:
            self.log(f"✗ 啟動 Edge 失敗：{e}")
            self.log("   請先安裝 Edge WebDriver（https://developer.microsoft.com/microsoft-edge/tools/webdriver/）")
            return False

        # ── stealth：25+ 項偽裝（覆蓋常見指紋偵測手法） ──
        stealth_js = r"""
        // 1. webdriver
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        delete Object.getPrototypeOf(navigator).webdriver;

        // 2. languages
        Object.defineProperty(navigator, 'languages', {get: () => ['zh-TW','zh','en-US','en']});

        // 3. plugins（模擬 5 個 plugin，PDF Viewer 等）
        Object.defineProperty(navigator, 'plugins', {get: () => {
            const p = [
                {name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                {name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
                {name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
                {name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: ''},
                {name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: ''},
            ];
            p.item = i => p[i]; p.namedItem = n => p.find(x=>x.name===n); p.refresh = ()=>{};
            return p;
        }});

        // 4. mimeTypes
        Object.defineProperty(navigator, 'mimeTypes', {get: () => [
            {type: 'application/pdf', suffixes: 'pdf', description: ''},
            {type: 'text/pdf', suffixes: 'pdf', description: ''}
        ]});

        // 5. chrome 物件
        if (!window.chrome) window.chrome = {};
        window.chrome.runtime = window.chrome.runtime || { OnInstalledReason:{}, OnRestartRequiredReason:{}, PlatformOs:{}, PlatformArch:{}, RequestUpdateCheckStatus:{} };
        window.chrome.app = window.chrome.app || { isInstalled: false, InstallState:{}, RunningState:{} };
        window.chrome.csi = window.chrome.csi || function() { return { startE: Date.now(), onloadT: Date.now(), pageT: Date.now(), tran: 15 }; };
        window.chrome.loadTimes = window.chrome.loadTimes || function() { return { requestTime: Date.now()/1000, startLoadTime: Date.now()/1000, commitLoadTime: Date.now()/1000, finishDocumentLoadTime: Date.now()/1000, finishLoadTime: Date.now()/1000, firstPaintTime: Date.now()/1000, firstPaintAfterLoadTime: 0, navigationType: 'Other', wasFetchedViaSpdy: false, wasNpnNegotiated: false, npnNegotiatedProtocol: 'unknown', wasAlternateProtocolAvailable: false, connectionInfo: 'http/1.1' }; };

        // 6. permissions query mock（notifications 修正常見偵測點）
        const origQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (origQuery) {
            window.navigator.permissions.query = (p) => p.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : origQuery(p);
        }

        // 7~10. screen / hardware
        Object.defineProperty(screen, 'colorDepth', {get: () => 24});
        Object.defineProperty(screen, 'pixelDepth', {get: () => 24});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
        Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});

        // 11. userAgentData（Edge 新版指紋）
        Object.defineProperty(navigator, 'userAgentData', {get: () => ({
            brands: [
                {brand: 'Microsoft Edge', version: '130'},
                {brand: 'Chromium', version: '130'},
                {brand: 'Not?A_Brand', version: '99'},
            ],
            mobile: false,
            platform: 'Windows',
            getHighEntropyValues: () => Promise.resolve({
                architecture: 'x86', bitness: '64', model: '',
                platformVersion: '15.0.0', uaFullVersion: '130.0.0.0',
                fullVersionList: [{brand:'Microsoft Edge',version:'130.0.0.0'},{brand:'Chromium',version:'130.0.0.0'}]
            })
        })});

        // 12. WebGL vendor / renderer 偽裝
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Intel Inc.';
            if (p === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, p);
        };
        if (window.WebGL2RenderingContext) {
            const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(p) {
                if (p === 37445) return 'Intel Inc.';
                if (p === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter2.call(this, p);
            };
        }

        // 13. Canvas 微擾動（避免 fingerprint hash 命中黑名單）
        const toDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(...a) {
            const ctx = this.getContext('2d');
            if (ctx) { ctx.fillStyle = 'rgba(0,0,0,0.005)'; ctx.fillRect(0,0,1,1); }
            return toDataURL.apply(this, a);
        };

        // 14. battery API mock（很多偵測會問）
        if (navigator.getBattery) {
            navigator.getBattery = () => Promise.resolve({
                charging: true, chargingTime: 0, dischargingTime: Infinity, level: 1,
                addEventListener: ()=>{}, removeEventListener: ()=>{}, dispatchEvent: ()=>true
            });
        }

        // 15. connection
        Object.defineProperty(navigator, 'connection', {get: () => ({
            effectiveType: '4g', rtt: 50, downlink: 10, saveData: false,
            addEventListener: ()=>{}, removeEventListener: ()=>{}
        })});

        // 16. mediaDevices
        if (navigator.mediaDevices) {
            const enumOrig = navigator.mediaDevices.enumerateDevices;
            navigator.mediaDevices.enumerateDevices = () => Promise.resolve([
                {deviceId:'default', kind:'audioinput', label:'', groupId:''},
                {deviceId:'default', kind:'audiooutput', label:'', groupId:''},
            ]);
        }

        // 17. AudioContext 微擾（指紋擾動）
        if (window.AudioContext || window.webkitAudioContext) {
            const AC = window.AudioContext || window.webkitAudioContext;
            const orig = AC.prototype.createOscillator;
            AC.prototype.createOscillator = function() {
                const osc = orig.apply(this, arguments);
                const f = osc.frequency.value;
                osc.frequency.value = f + Math.random() * 0.0001;
                return osc;
            };
        }

        // 18. cookieEnabled / doNotTrack
        Object.defineProperty(navigator, 'cookieEnabled', {get: () => true});
        Object.defineProperty(navigator, 'doNotTrack', {get: () => null});

        // 19. webdriver in iframes（contentWindow 也要清）
        const _addEventListener = document.addEventListener;
        document.addEventListener = function(t, fn, ...rest) {
            if (t === 'DOMContentLoaded') {
                document.querySelectorAll('iframe').forEach(f => {
                    try {
                        Object.defineProperty(f.contentWindow.navigator, 'webdriver', {get:()=>undefined});
                    } catch(_){}
                });
            }
            return _addEventListener.call(this, t, fn, ...rest);
        };

        // 20. 隱藏 Function.prototype.toString 對 native function 的洩漏
        const nativeToString = Function.prototype.toString;
        Function.prototype.toString = function() {
            if (this === Function.prototype.toString) return nativeToString.call(this);
            return nativeToString.call(this);
        };

        // 21. Notification.permission（避免 'denied' 露餡）
        if (window.Notification) {
            Object.defineProperty(Notification, 'permission', {get: () => 'default'});
        }

        // 22. window.outerWidth/Height 補正（無頭模式露餡防呆）
        if (window.outerWidth === 0) {
            Object.defineProperty(window, 'outerWidth', {get: () => window.innerWidth});
            Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight});
        }

        // 23. 移除 cdc_*/$cdc_* 變數（webdriver 洩漏點）
        for (const k of Object.keys(window)) {
            if (/^[$_]?cdc_/.test(k) || /^[$_]?wdc_/.test(k)) {
                try { delete window[k]; } catch(_){}
            }
        }

        // 24. CSS prefers-color-scheme 等媒體查詢
        // 保留瀏覽器預設

        // 25. 移除 navigator.bluetooth（automation 通常沒有）
        try { delete Navigator.prototype.bluetooth; } catch(_){}
        """
        try:
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
        except Exception:
            pass
        # 額外：CDP 改 User-Agent metadata
        try:
            self.driver.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
                "acceptLanguage": "zh-TW,zh;q=0.9,en;q=0.8",
                "platform": "Win32",
            })
        except Exception:
            pass
        return True

    def quit(self):
        if self.driver:
            try: self.driver.quit()
            except Exception: pass
            self.driver = None

    # ── 流程：取一筆 ──────────────────────────────
    def fetch_one(self, profile):
        """profile: dict — gender/nation/year/res18/resCur/orient/edu/testing"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait, Select
        from selenium.webdriver.support import expected_conditions as EC
        d = self.driver
        wait = WebDriverWait(d, self.dly.wait_timeout)

        # 1) 首頁 → 點「進入風險評估」
        d.get(URL_HOME)
        self.dly.page()
        try:
            link = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//*[contains(text(),'進入風險評估') or contains(text(),'風險評估')]")))
            try: link.click()
            except Exception:
                d.execute_script("arguments[0].click();", link)
        except Exception as e:
            self.log(f"⚠ 找不到「進入風險評估」按鈕：{e}")
            save_debug_snapshot(d, "home_no_risk_btn")
            return None
        # 等到 Page 1 載入（只等 QuestWizard radio 出現；URL 條件已證實會誤判）
        try:
            WebDriverWait(d, self.dly.wait_timeout).until(
                lambda drv: len(drv.find_elements(By.XPATH,
                    "//input[@type='radio' and contains(@name,'QuestWizard')]")) > 0
            )
        except Exception:
            self.log("⚠ 進入問卷後，Page 1 沒等到 radio，續行嘗試")
        self.dly.action()

        # v1.0.22：偵測完整模式 — profile 含 q1_sex 即為完整 profile
        is_complete = "q1_sex" in profile

        # === Page 1 ===
        # v1.0.24：Q1=否 → 系統自動帶 Q2-5 = 沒有發生，只點 Q1 即可
        # Q1=是 才需要逐一填 Q2-Q5
        if is_complete:
            if profile.get("q1_sex") == "否":
                # 只點 Q1，避免動到系統自動填的 Q2-5
                p1_keys = [PAGE1_KEYS[0]]
            else:
                p1_keys = PAGE1_KEYS  # Q1=是 → 完整填 Q1-Q5
            n = self._fill_groups_in_order(profile, p1_keys)
            if n == 0:
                self.log("✗ Page 1 沒填到任何 radio"); save_debug_snapshot(d, "p1_fail"); return None
            recovery_p1 = lambda: self._fill_groups_in_order(profile, p1_keys)
        else:
            if not self._click_first_group_no():
                self.log("✗ Page 1 Q1 失敗"); save_debug_snapshot(d, "p1_q1_fail"); return None
            recovery_p1 = lambda: self._click_first_group_no()
        self.dly.action()
        if not self._click_next(expect_progress=15, recovery_fn=recovery_p1):
            save_debug_snapshot(d, "p1_to_p2_fail"); return None
        self.dly.page()

        # === Page 2 ===
        if is_complete:
            self._fill_groups_in_order(profile, [PAGE2_BASE_KEY])
            # 若 Q6=是 → 等子題出現再填
            if profile.get("q6_std") == "是":
                self.log("  ⏳ Q6=是，等子題展開…")
                from selenium.webdriver.support.ui import WebDriverWait
                try:
                    WebDriverWait(d, self.dly.wait_timeout).until(
                        lambda drv: len(self._get_questwizard_groups()) >= 1 + len(PAGE2_SUB_KEYS) - 2
                    )
                    self._fill_groups_in_order(profile, PAGE2_SUB_KEYS, start_index=1)
                    # text 欄位
                    self._fill_text_input_by_label("原因", profile.get("q6_hiv_reason", ""))
                    self._fill_text_input_by_label("其他", profile.get("q6_other", ""))
                except Exception as e:
                    self.log(f"  ⚠ Q6 子題填寫失敗：{e}")
            recovery_p2 = lambda: self._fill_groups_in_order(profile, [PAGE2_BASE_KEY])
        else:
            if not self._click_first_group_no():
                self.log("✗ Page 2 Q6 失敗"); save_debug_snapshot(d, "p2_q6_fail"); return None
            recovery_p2 = lambda: self._click_first_group_no()
        self.dly.action()
        if not self._click_next(expect_progress=30, recovery_fn=recovery_p2):
            save_debug_snapshot(d, "p2_to_p3_fail"); return None
        self.dly.page()

        # === Page 3 ===
        if is_complete:
            self._fill_groups_in_order(profile, [PAGE3_BASE_KEY])
            if profile.get("q7_drug_use") == "是":
                self.log("  ⏳ Q7=是，等子題展開…")
                from selenium.webdriver.support.ui import WebDriverWait
                try:
                    WebDriverWait(d, self.dly.wait_timeout).until(
                        lambda drv: len(self._get_questwizard_groups()) >= 1 + len(PAGE3_SUB_KEYS) - 2
                    )
                    self._fill_groups_in_order(profile, PAGE3_SUB_KEYS, start_index=1)
                    self._fill_text_input_by_label("其他", profile.get("q7_other", ""))
                    # v1.0.23 安非他命使用方式：多 checkbox（吸入/注射/口服）
                    if profile.get("q7_amph") == "是":
                        method_str = profile.get("q7_amph_method", "") or ""
                        methods = [s.strip() for s in str(method_str).replace("，", ",").split(",") if s.strip()]
                        if methods:
                            n = self._click_checkboxes_by_text(methods)
                            self.log(f"  ✓ 安非他命使用方式 勾選 {n}/{len(methods)} 項")
                except Exception as e:
                    self.log(f"  ⚠ Q7 子題填寫失敗：{e}")
            recovery_p3 = lambda: self._fill_groups_in_order(profile, [PAGE3_BASE_KEY])
        else:
            if not self._click_first_group_no():
                self.log("✗ Page 3 Q7 失敗"); save_debug_snapshot(d, "p3_q7_fail"); return None
            recovery_p3 = lambda: self._click_first_group_no()
        self.dly.action()
        if not self._click_next(expect_progress=45, recovery_fn=recovery_p3):
            save_debug_snapshot(d, "p3_to_p4_fail"); return None
        self.dly.page()

        # === Page 4 ===
        if is_complete:
            n = self._fill_groups_in_order(profile, PAGE4_KEYS)
            if n < 4:
                self.log(f"⚠ Page 4 只填到 {n}/4");
            recovery_p4 = lambda: self._fill_groups_in_order(profile, PAGE4_KEYS)
        else:
            if not self._click_all_groups_no(expected_n=4):
                self.log("✗ Page 4 Q8 失敗"); save_debug_snapshot(d, "p4_q8_fail"); return None
            recovery_p4 = lambda: self._click_all_groups_no(expected_n=4)
        if not self._click_next(expect_progress=60, recovery_fn=recovery_p4):
            save_debug_snapshot(d, "p4_to_p5_fail"); return None
        self.dly.page()

        # === Page 5 ===
        if is_complete:
            n = self._fill_groups_in_order(profile, PAGE5_KEYS)
            if n < 5:
                self.log(f"⚠ Page 5 只填到 {n}/5")
            recovery_p5 = lambda: self._fill_groups_in_order(profile, PAGE5_KEYS)
        else:
            if not self._click_all_groups_no(expected_n=5):
                self.log("✗ Page 5 Q9~13 失敗"); save_debug_snapshot(d, "p5_q9to13_fail"); return None
            recovery_p5 = lambda: self._click_all_groups_no(expected_n=5)
        if not self._click_next(expect_progress=75,
                                expect_element_id="ctl00_MainContent_QuestWizard_BirthYear",
                                recovery_fn=recovery_p5):
            save_debug_snapshot(d, "p5_to_p6_fail"); return None
        self.dly.page()

        # === Page 6 — 基本資料（v1.0.32：實際 radio name 為「心理性別」非「性別」） ===
        gender_map = {"男": "1", "女": "2", "跨性別": "3", "其他": "4"}
        nation_map = {"本國籍": "1", "外國籍": "2"}
        if not self._set_radio_by_name("ctl00$MainContent$QuestWizard$心理性別",
                                        gender_map.get(profile["gender"], "1")):
            self._click_label_text(profile["gender"])
        self.dly.action()
        if not self._set_radio_by_name("ctl00$MainContent$QuestWizard$國籍",
                                        nation_map.get(profile["nation"], "1")):
            self._click_label_text(profile["nation"])
        self.dly.action()
        # 6-2 出生年（ASP.NET PostBack 控件，選了會回 server 重畫頁面）
        if not self._select_by_id("ctl00_MainContent_QuestWizard_BirthYear", str(profile["year"])):
            self.log("✗ Page 6 出生年下拉失敗"); save_debug_snapshot(d, "p6_year_fail"); return None
        # 等 PostBack 完成 — 18歲前居住地會從 disabled 變 enabled
        self.log(f"  ⏳ 出生年 {profile['year']} 已選，等 PostBack…")
        if not self._wait_for_enabled("ctl00_MainContent_QuestWizard_DDL_Live_18_ago"):
            save_debug_snapshot(d, "p6_postback_timeout"); return None
        self.dly.action()
        # 6-3 18 歲以前居住地
        if not self._select_by_id("ctl00_MainContent_QuestWizard_DDL_Live_18_ago", profile["res18"]):
            self.log("✗ Page 6 18歲前居住地失敗"); save_debug_snapshot(d, "p6_res18_fail"); return None
        self.dly.action()
        # 6-4 現居住地（id 是「居住地」中文）
        if not self._select_by_id("ctl00_MainContent_QuestWizard_居住地", profile["resCur"]):
            self.log("✗ Page 6 現居住地失敗"); save_debug_snapshot(d, "p6_resCur_fail"); return None
        self.dly.action()
        # 6-5 性傾向（name + value：1=同 2=雙 3=異）
        orient_map = {"同性": "1", "雙性": "2", "異性": "3"}
        if not self._set_radio_by_name("ctl00$MainContent$QuestWizard$性傾向",
                                        orient_map.get(profile["orient"], "3")):
            save_debug_snapshot(d, "p6_orient_fail"); return None
        self.dly.action()
        # 6-6 教育程度（name + value：1=不識字 2=國中以下 3=高中職 4=專科或大學 5=研究所）
        edu_map = {"不識字": "1", "國中以下": "2", "高中職": "3", "專科或大學": "4", "研究所(含)以上": "5"}
        if not self._set_radio_by_name("ctl00$MainContent$QuestWizard$教育程度",
                                        edu_map.get(profile["edu"], "3")):
            save_debug_snapshot(d, "p6_edu_fail"); return None
        self.dly.action()
        if not self._click_next(expect_progress=90):
            self._dismiss_alert_if_any(); save_debug_snapshot(d, "p6_to_p7_fail"); return None
        self.dly.page()

        # === Page 7 — 篩檢習慣 + 聯絡方式（v1.0.32：用實際 name + value） ===
        if is_complete:
            # testing_habit (rdlRegularScreening): 1=是 / 2=否 / 0=從未做過
            th_map = {"是": "1", "否": "2", "從未做過": "0"}
            tval = th_map.get(profile.get("testing_habit", "否"), "2")
            if not self._set_radio_by_name("ctl00$MainContent$QuestWizard$rdlRegularScreening", tval):
                # fallback：原 fill_groups
                self._fill_groups_in_order(profile, [PAGE7_BASE_KEY])
            # text 欄位用 name 精確定位（v1.0.32 修正：之前 label 鄰近搜尋抓不到）
            for key, name in [("phone",         "ctl00$MainContent$QuestWizard$txtMobile"),
                               ("email",         "ctl00$MainContent$QuestWizard$txtEMail"),
                               ("other_contact", "ctl00$MainContent$QuestWizard$txtOther")]:
                v = profile.get(key, "")
                if v:
                    self._fill_text_input_by_name(name, v)
        else:
            choice = profile.get("testing", "否")
            if choice == "從未做過":
                self._click_label_text("從未做過")
            else:
                self._click_label_text("否")
        self.dly.action()
        if not self._click_done():
            return None
        self.dly.page()

        # === 結果頁：抓代碼（伺服器繁忙時可能要等較久） ===
        try:
            WebDriverWait(d, self.dly.wait_timeout * 2).until(EC.presence_of_element_located(
                (By.XPATH, "//*[contains(text(),'諮詢代碼')]")))
        except Exception:
            self.log("⚠ 沒等到結果頁")
            save_debug_snapshot(d, "result_page_timeout")
            return None
        # v1.0.12：DEBUG 模式存前 2 筆成功的結果頁，方便驗證代碼讀對位置
        if DEBUG and getattr(self, "_result_sample_count", 0) < 2:
            save_debug_snapshot(d, f"result_page_sample_{getattr(self, '_result_sample_count', 0) + 1}")
            self._result_sample_count = getattr(self, "_result_sample_count", 0) + 1
        # v1.0.12：用 body.text 抽（避開 HTML noise / 隱藏 script 文字）
        import re
        try:
            body_text = d.find_element(By.TAG_NAME, "body").text
        except Exception:
            body_text = d.page_source
        # A) 「諮詢代碼為"XXXXXX"」最常見格式
        m = re.search(r'諮詢代碼為["\'“”「]?\s*([0-9A-Z\-]{4,})', body_text)
        # B) 「諮詢代碼: XXXXXX」備用格式
        if not m:
            m = re.search(r'諮詢代碼[\s::是為]+["\'“”「]?\s*([0-9A-Z\-]{4,})', body_text)
        # C) 找「諮詢代碼」之後 30 字內的數字串
        if not m:
            m = re.search(r'諮詢代碼[^0-9]{0,30}([0-9]{4,})', body_text)
        if not m:
            self.log("⚠ 無法解析諮詢代碼，body.text 開頭：" + body_text[:200].replace("\n", " | "))
            save_debug_snapshot(d, "code_not_found")
            return None
        return m.group(1)

    # ── DOM helpers ──────────────────────────────
    def _click_next_btn(self):
        """純粹按下一步按鈕，不做進度驗證"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        d = self.driver
        try:
            btn = WebDriverWait(d, self.dly.wait_timeout).until(EC.element_to_be_clickable(
                (By.XPATH, "//*[(self::button or self::a or self::input) and (contains(.,'下一步') or @value='下一步')]")))
            try: btn.click()
            except Exception: d.execute_script("arguments[0].click();", btn)
            return True
        except Exception as e:
            self.log(f"✗ 找不到下一步按鈕：{e}")
            return False

    def _click_next(self, expect_progress=None, expect_element_id=None,
                    timeout=None, recovery_fn=None, max_retries=2):
        """v1.0.13：雙錨點等待 — 完成度% AND 目標元素 ID 都要到位才算換頁成功。
           單看完成度會被誤導：伺服器有時驗證失敗仍把 % 文字提前更新。
           失敗時最多重試 max_retries 次（每次都呼叫 recovery_fn 補填）。"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        d = self.driver
        to = timeout if timeout is not None else self.dly.wait_timeout

        for attempt in range(max_retries + 1):
            if not self._click_next_btn():
                return False
            ok = True
            if expect_progress is not None:
                ok = self._wait_for_progress(expect_progress, timeout=to)
            if ok and expect_element_id is not None:
                # 二次驗證：目標元素是否真的出現（防進度文字提前但畫面沒切的偽陽性）
                try:
                    WebDriverWait(d, 8).until(EC.presence_of_element_located(
                        (By.ID, expect_element_id)))
                except Exception:
                    self.log(f"⚠ 完成度到了但找不到 #{expect_element_id} — 換頁假性成功")
                    ok = False
            if ok:
                if attempt > 0:
                    self.log(f"✓ 第 {attempt+1} 次嘗試成功")
                return True
            # ── 失敗：偵測+關閉彈窗、補填、重試 ──
            self.log(f"⚠ 換頁失敗（第 {attempt+1} 次），啟動恢復…")
            self._dismiss_alert_if_any()
            time.sleep(0.5)
            if recovery_fn:
                try:
                    self.log("  ↻ 重新填寫本頁缺漏…")
                    recovery_fn()
                except Exception as e:
                    self.log(f"  ↻ recovery_fn 失敗：{e}")
            time.sleep(0.3)
        self.log(f"✗ 重試 {max_retries} 次仍無法換頁，放棄本筆")
        return False

    def _wait_for_progress(self, target_pct, timeout=None):
        """等到頁面顯示「完成度：X %」=target_pct（精確錨點頁面切換）
           多人連線時頁面回應會慢，timeout 從 GUI 設定（預設 30s）"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        d = self.driver
        to = timeout if timeout is not None else self.dly.wait_timeout
        try:
            WebDriverWait(d, to).until(
                lambda drv: f"{int(target_pct)} %" in drv.page_source
                         or f"{int(target_pct)}%" in drv.page_source
            )
            return True
        except Exception as e:
            self.log(f"✗ 等不到完成度 {target_pct}%（伺服器繁忙？逾時 {to}s）")
            save_debug_snapshot(d, f"progress_{target_pct}_timeout")
            return False

    def _click_done(self):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        d = self.driver
        try:
            btn = WebDriverWait(d, 15).until(EC.element_to_be_clickable(
                (By.XPATH, "//*[(self::button or self::a or self::input) and (contains(.,'完成') or @value='完成')]")))
            btn.click()
            return True
        except Exception as e:
            self.log(f"✗ 完成失敗：{e}")
            return False

    # ── v1.0.22 完整模式輔助：依 profile 設定每個 radio group ──
    @staticmethod
    def _option_value(key, value_str):
        """選項字串 → ASP.NET radio value
           v1.0.32：hiva 對「沒有發生」/「從未做過」固定用 value=0，不是 index+1，需特別處理"""
        s = str(value_str or "").strip()
        # 特殊值優先（hiva ASP.NET 慣例）
        if s in ("沒有發生", "從未做過"):
            return "0"
        allowed = COMPLETE_FIELD_ALLOWED.get(key)
        if not allowed:
            return None
        if s in allowed:
            return str(allowed.index(s) + 1)
        if s == "是": return "1"
        if s == "否": return "2"
        if s == "不確定": return "3"
        return None

    def _get_questwizard_groups(self):
        """取得頁面上所有 QuestWizard radio 的 group name 列表（依 DOM 順序，去重）"""
        from selenium.webdriver.common.by import By
        radios = self.driver.find_elements(By.XPATH,
            "//input[@type='radio' and contains(@name,'QuestWizard')]")
        seen = set(); ordered = []
        for r in radios:
            n = r.get_attribute("name") or ""
            if n and n not in seen:
                seen.add(n); ordered.append(n)
        return ordered

    def _set_radio_for_group(self, name, value):
        """指定 group name + value 點選 radio"""
        from selenium.webdriver.common.by import By
        d = self.driver
        try:
            target = d.find_element(By.XPATH,
                f"//input[@type='radio' and @name=\"{name}\" and @value=\"{value}\"]")
            d.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
            try: target.click()
            except Exception: d.execute_script("arguments[0].click();", target)
            return True
        except Exception:
            return False

    def _fill_groups_in_order(self, profile, field_keys, start_index=0):
        """依 DOM 順序給每個 group 設定（從 start_index 起對應 field_keys[0]）"""
        ordered = self._get_questwizard_groups()
        n_filled = 0
        for i, name in enumerate(ordered):
            if i < start_index:
                continue
            idx = i - start_index
            if idx >= len(field_keys):
                break
            key = field_keys[idx]
            val = profile.get(key)
            if val is None or val == "":
                # 缺值：依預設
                for k, _, dv, _ in COMPLETE_FIELDS:
                    if k == key:
                        val = dv; break
            radio_value = self._option_value(key, val)
            if radio_value is None:
                self.log(f"  ⚠ {key}={val} 沒有對應 value，跳過")
                continue
            if self._set_radio_for_group(name, radio_value):
                n_filled += 1
                self.dly.action()
            else:
                self.log(f"  ✗ {key}={val} (value={radio_value}) 點選失敗")
        return n_filled

    def _click_checkboxes_by_text(self, texts):
        """v1.0.23：勾選頁面上 label 文字含 texts 任一字的 checkbox（用於安非他命使用方式：吸入/注射/口服）"""
        if not texts: return 0
        from selenium.webdriver.common.by import By
        d = self.driver
        n = 0
        for t in texts:
            try:
                xp = f"//input[@type='checkbox' and (following-sibling::*[contains(.,'{t}')] or @value='{t}')]"
                els = d.find_elements(By.XPATH, xp)
                if not els:
                    xp2 = f"//*[contains(text(),'{t}')]/preceding-sibling::input[@type='checkbox'][1] | //*[contains(text(),'{t}')]/parent::*//input[@type='checkbox']"
                    els = d.find_elements(By.XPATH, xp2)
                if els:
                    el = els[0]
                    if not el.is_selected():
                        try: el.click()
                        except Exception: d.execute_script("arguments[0].click();", el)
                    n += 1
            except Exception:
                continue
        return n

    def _fill_text_input_by_name(self, name, value):
        """v1.0.32：以 name 屬性精準定位 input + 輸入文字"""
        if not value: return False
        from selenium.webdriver.common.by import By
        d = self.driver
        try:
            el = d.find_element(By.NAME, name)
            el.clear()
            el.send_keys(str(value))
            return True
        except Exception as e:
            self.log(f"  ⚠ 找不到 input name={name}：{e}")
            return False

    def _fill_text_input_by_label(self, label_text, value):
        """找 label 文字旁邊的 input[type=text]，輸入 value"""
        if not value: return False
        from selenium.webdriver.common.by import By
        d = self.driver
        try:
            xp = f"//*[contains(text(),'{label_text}')]/following::input[@type='text' or @type='email' or @type='tel'][1]"
            el = d.find_element(By.XPATH, xp)
            el.clear()
            el.send_keys(str(value))
            return True
        except Exception as e:
            self.log(f"  ⚠ 找不到 {label_text} 對應的 input：{e}")
            return False

    def _click_all_groups_no(self, expected_n=None, value="2"):
        """ASP.NET QuestWizard：頁面上每組 radio 由 name="ctl00$MainContent$QuestWizard$XXXX" 區隔；
           每組點 value=value（否=2 / 是=1）。
           expected_n：若指定，至少要點到這麼多組才算成功。"""
        from selenium.webdriver.common.by import By
        d = self.driver
        try:
            # 抓所有 QuestWizard radio，依 name 分組（保留 DOM 出現順序）
            radios = d.find_elements(By.XPATH,
                "//input[@type='radio' and contains(@name,'QuestWizard')]")
            seen = set()
            ordered_names = []
            for r in radios:
                n = r.get_attribute("name") or ""
                if n and n not in seen:
                    seen.add(n)
                    ordered_names.append(n)
            count = 0
            for name in ordered_names:
                # 該 group 的 value=否 那顆
                try:
                    target = d.find_element(By.XPATH,
                        f"//input[@type='radio' and @name=\"{name}\" and @value='{value}']")
                except Exception:
                    continue
                d.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
                try: target.click()
                except Exception: d.execute_script("arguments[0].click();", target)
                count += 1
                self.dly.action()
            if expected_n is not None and count < expected_n:
                self.log(f"⚠ 預期至少點 {expected_n} 組 radio，實際只點到 {count} 組")
                return False
            return count > 0
        except Exception as e:
            self.log(f"✗ click_all_groups_no 失敗：{e}")
            return False

    def _click_first_group_no(self, value="2"):
        """只點頁面上第一組 QuestWizard radio 的 value（給 Page 1/2/3 用）"""
        from selenium.webdriver.common.by import By
        d = self.driver
        try:
            radios = d.find_elements(By.XPATH,
                "//input[@type='radio' and contains(@name,'QuestWizard')]")
            if not radios:
                return False
            first_name = radios[0].get_attribute("name")
            target = d.find_element(By.XPATH,
                f"//input[@type='radio' and @name=\"{first_name}\" and @value='{value}']")
            d.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
            try: target.click()
            except Exception: d.execute_script("arguments[0].click();", target)
            return True
        except Exception as e:
            self.log(f"✗ first group radio 點選失敗：{e}")
            return False

    def _dismiss_alert_if_any(self):
        """若頁面跳出 alert / Modal（例如「請於紅處填入您的答案」），按確定關閉"""
        from selenium.common.exceptions import NoAlertPresentException
        d = self.driver
        # JS alert
        try:
            a = d.switch_to.alert
            self.log(f"⚠ 偵測到瀏覽器 alert：{a.text[:80]}")
            a.accept()
            return True
        except NoAlertPresentException:
            pass
        except Exception:
            pass
        # 自訂 Modal（含 SweetAlert / Bootstrap modal）
        from selenium.webdriver.common.by import By
        for xp in [
            "//div[contains(@class,'swal') or contains(@class,'modal')]"
            "//button[contains(.,'確定') or contains(.,'OK') or contains(.,'關閉')]",
        ]:
            try:
                btn = d.find_element(By.XPATH, xp)
                if btn.is_displayed():
                    self.log("⚠ 偵測到自訂彈窗，按確定關閉")
                    btn.click()
                    return True
            except Exception:
                continue
        return False

    def _click_label_text(self, text):
        """Page 6/7：找文字含 text 的 label 點擊（含其前 radio）"""
        from selenium.webdriver.common.by import By
        d = self.driver
        try:
            els = d.find_elements(By.XPATH,
                f"//label[contains(.,'{text}')] | //*[contains(text(),'{text}')]/preceding-sibling::input[@type='radio'][1]")
            for el in els:
                try:
                    d.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    try: el.click()
                    except Exception: d.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    continue
            return False
        except Exception as e:
            self.log(f"  label click 失敗 text={text}: {e}")
            return False

    def _select_by_id(self, elem_id, text):
        """用元素 ID 直接定位 <select>，依 visible text 選擇選項。
           Page 6 表單頭部有日曆 widget 的隱藏 select，會把 index 推掉，必須用 ID。"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select
        d = self.driver
        try:
            el = d.find_element(By.ID, elem_id)
            try:
                Select(el).select_by_visible_text(text)
                return True
            except Exception:
                for opt in el.find_elements(By.TAG_NAME, "option"):
                    if text == (opt.text or "").strip() or text in (opt.text or ""):
                        opt.click()
                        return True
                return False
        except Exception as e:
            self.log(f"✗ 找不到下拉 id={elem_id}：{e}")
            return False

    def _set_radio_by_name(self, name_attr, value):
        """以 name 屬性 + value 直接設定該題的 radio"""
        from selenium.webdriver.common.by import By
        d = self.driver
        try:
            xp = f"//input[@type='radio' and @name=\"{name_attr}\" and @value=\"{value}\"]"
            el = d.find_element(By.XPATH, xp)
            d.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try: el.click()
            except Exception: d.execute_script("arguments[0].click();", el)
            return True
        except Exception as e:
            self.log(f"✗ radio name={name_attr} value={value} 點選失敗：{e}")
            return False

    def _wait_for_enabled(self, elem_id, timeout=None):
        """等到指定元素的 disabled 屬性消失（ASP.NET PostBack 完成的錨點）"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        d = self.driver
        to = timeout if timeout is not None else self.dly.wait_timeout
        try:
            WebDriverWait(d, to).until(
                lambda drv: not drv.find_element(By.ID, elem_id).get_attribute("disabled")
            )
            return True
        except Exception as e:
            self.log(f"✗ 等不到 {elem_id} 變 enabled：{e}")
            return False


# ── GUI ──────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        root.title(f"HIV 匿名諮詢代碼批次取號 v{VERSION}")
        root.geometry("1180x1050")
        root.minsize(960, 720)
        try:
            root.state("zoomed")
        except Exception:
            pass
        self.stop_evt  = threading.Event()
        self.pause_evt = threading.Event()
        self.results = []
        self.worker = None
        self.thread = None
        self._current_xlsx_path = None
        self._balancing = False
        self.output_dir_var = tk.StringVar(value=OUTPUT_DIR)
        self.theme_var = tk.StringVar(value=DEFAULT_THEME)
        self._spinner_running = False
        self._spinner_idx = 0
        self.mode_var = tk.StringVar(value="簡易（比例分布）")
        self._imported_profiles = []
        self._current_pool = []
        self._completed_index = 0
        self._build()
        self._apply_theme(DEFAULT_THEME)
        self._load_settings()
        # v1.0.21 啟動後檢查續傳
        self._check_resume()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── v1.0.8 比例自動平衡：調一個其他自動降低，總和恆 100% ──
    def _balance_pct(self, block, changed_var):
        if self._balancing:
            return
        self._balancing = True
        try:
            try:
                new_val = int(changed_var.get())
            except Exception:
                new_val = 0
            new_val = max(0, min(100, new_val))
            if new_val != changed_var.get():
                changed_var.set(new_val)
            others = [(op, v, cnt) for (op, v, cnt) in block if v is not changed_var]
            if not others:
                return
            other_sum = sum(int(v.get() or 0) for _, v, _ in others)
            remain = 100 - new_val
            remain = max(0, remain)
            if other_sum > 0:
                # 比例縮放剩餘者；最後一個吃掉 rounding 差
                running = 0
                for (op, v, cnt) in others[:-1]:
                    nv = int(round(int(v.get() or 0) * remain / other_sum))
                    v.set(nv); running += nv
                others[-1][1].set(max(0, remain - running))
            else:
                # 其他全是 0：剩餘者平均分配
                n = len(others)
                base = remain // n
                rem  = remain - base * n
                for i, (op, v, cnt) in enumerate(others):
                    v.set(base + (1 if i < rem else 0))
        finally:
            self._balancing = False
        self._update_counts()

    def _balance_city(self, rows, changed_pv):
        """居住地動態列：和上面同樣邏輯，總和恆 100%"""
        if self._balancing:
            return
        self._balancing = True
        try:
            try:
                new_val = int(changed_pv.get())
            except Exception:
                new_val = 0
            new_val = max(0, min(100, new_val))
            if new_val != changed_pv.get():
                changed_pv.set(new_val)
            others = [(cv, pv, fr) for (cv, pv, fr) in rows if pv is not changed_pv]
            if not others:
                if new_val != 100:
                    changed_pv.set(100)
                return
            other_sum = sum(int(pv.get() or 0) for _, pv, _ in others)
            remain = max(0, 100 - new_val)
            if other_sum > 0:
                running = 0
                for (cv, pv, fr) in others[:-1]:
                    nv = int(round(int(pv.get() or 0) * remain / other_sum))
                    pv.set(nv); running += nv
                others[-1][1].set(max(0, remain - running))
            else:
                n = len(others)
                base = remain // n
                rem  = remain - base * n
                for i, (cv, pv, fr) in enumerate(others):
                    pv.set(base + (1 if i < rem else 0))
        finally:
            self._balancing = False
        self._update_counts()

    def _build(self):
        pad = {"padx": 6, "pady": 4}
        # ── v1.0.15 輸出路徑（最上方一列） ──
        path_fr = ttk.LabelFrame(self.root, text="輸出資料夾（log / Excel / debug 都會放這）")
        path_fr.pack(fill="x", **pad)
        ttk.Entry(path_fr, textvariable=self.output_dir_var, width=70).pack(side="left", padx=4, pady=4, fill="x", expand=True)
        ttk.Button(path_fr, text="📂 瀏覽…", command=self._pick_output_dir).pack(side="left", padx=2)
        ttk.Button(path_fr, text="✅ 套用", command=self._apply_output_dir).pack(side="left", padx=2)
        ttk.Button(path_fr, text="↩ 還原預設", command=self._reset_output_dir).pack(side="left", padx=2)
        ttk.Label(path_fr, text="  🎨 主題：").pack(side="left", padx=(12, 2))
        theme_cb = ttk.Combobox(path_fr, textvariable=self.theme_var, state="readonly",
                                 values=list(THEMES.keys()), width=14)
        theme_cb.pack(side="left", padx=2)
        theme_cb.bind("<<ComboboxSelected>>", lambda e: self._apply_theme(self.theme_var.get()))

        # ── v1.0.19 Notebook：批次 / 單筆 兩個分頁 ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=2)
        # v1.0.26：分頁切換時更新底部按鈕標籤
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._update_start_btn_label())

        tab_batch  = ttk.Frame(self.notebook)
        tab_single = ttk.Frame(self.notebook)
        self.notebook.add(tab_batch,  text="📊 批次取號")
        self.notebook.add(tab_single, text="🎯 單筆取號")

        # ── v1.0.21 模式切換列（批次分頁最頂） ──
        mode_fr = ttk.LabelFrame(tab_batch, text="🎛 取號模式")
        mode_fr.pack(fill="x", padx=6, pady=4)
        ttk.Radiobutton(mode_fr, text="📊 簡易（比例分布隨機）", variable=self.mode_var,
                        value="簡易（比例分布）", command=self._on_mode_change).pack(side="left", padx=10, pady=6)
        ttk.Radiobutton(mode_fr, text="📋 完整（匯入 xlsx 逐筆指定）", variable=self.mode_var,
                        value="完整（匯入xlsx）", command=self._on_mode_change).pack(side="left", padx=10, pady=6)
        ttk.Button(mode_fr, text="📤 下載範例 xlsx",
                   command=self._export_sample_xlsx).pack(side="left", padx=12)
        ttk.Button(mode_fr, text="📥 匯入 xlsx",
                   command=self._import_xlsx).pack(side="left", padx=4)
        self.import_status_var = tk.StringVar(value="（未匯入）")
        ttk.Label(mode_fr, textvariable=self.import_status_var,
                  foreground="#1565c0", font=("微軟正黑體", 9, "bold")).pack(side="left", padx=12)

        # ── 批次取號分頁內容 ──
        # v1.0.23：完整模式專用預覽列表（簡易模式時隱藏）
        preview_fr = ttk.LabelFrame(tab_batch, text="📋 已匯入預覽（顯示主要欄位）")
        cols = ("#", "性別", "出生年", "18歲前", "現居", "性傾向", "教育", "Q1性行為", "Q6性病", "Q7藥物", "P7篩檢")
        widths = (40, 60, 70, 90, 90, 70, 100, 70, 60, 60, 80)
        self.preview_tree = ttk.Treeview(preview_fr, columns=cols, show="headings", height=8)
        for c, w in zip(cols, widths):
            self.preview_tree.heading(c, text=c)
            self.preview_tree.column(c, width=w, anchor="center")
        self.preview_tree.pack(fill="x", padx=4, pady=4)
        self._batch_preview_fr = preview_fr
        # 簡易模式時不要 pack 顯示

        topbar = ttk.Frame(tab_batch)
        topbar.pack(fill="x", **pad)
        self._batch_topbar = topbar  # 完整模式時要隱藏
        midbar = ttk.Frame(tab_batch)
        midbar.pack(fill="x", **pad)
        self._batch_midbar = midbar  # 完整模式時要隱藏
        col_left  = ttk.Frame(midbar)
        col_right = ttk.Frame(midbar)
        col_left.pack(side="left",  fill="both", expand=True, padx=(0, 4))
        col_right.pack(side="left", fill="both", expand=True, padx=(4, 0))

        # ─ 基本設定 ─
        top = ttk.LabelFrame(topbar, text="基本設定（批次）")
        top.pack(side="left", padx=(0, 4))
        ttk.Label(top, text="總筆數：").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self.total_var = tk.IntVar(value=30)
        e_total = self._mk_int_entry(top, self.total_var, width=8, justify="center")
        e_total.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.total_var.trace_add("write", lambda *a: self._update_counts())
        ttk.Label(top, text="出生年：").grid(row=0, column=2, padx=12, pady=4, sticky="w")
        self.year_lo = tk.IntVar(value=1971)
        self.year_hi = tk.IntVar(value=2008)
        self._mk_int_entry(top, self.year_lo, width=6, justify="center").grid(row=0, column=3, padx=2, pady=4)
        ttk.Label(top, text=" ~ ").grid(row=0, column=4)
        self._mk_int_entry(top, self.year_hi, width=6, justify="center").grid(row=0, column=5, padx=2, pady=4)

        # ─ 速度 / 延遲設定 ─
        speed = ttk.LabelFrame(topbar, text="速度 / 延遲設定（秒；隨機區間，避免太規律被偵測）")
        speed.pack(side="left", fill="x", expand=True, padx=(4, 0))
        # 預設 combo
        ttk.Label(speed, text="預設模式：").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        self.speed_preset = tk.StringVar(value="中（推薦）")
        cb = ttk.Combobox(speed, textvariable=self.speed_preset, width=14, state="readonly",
                          values=["快（偷懶模式）", "中（推薦）", "慢（高峰時段）", "極慢（嚴重塞車）", "自訂"])
        cb.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        cb.bind("<<ComboboxSelected>>", self._apply_speed_preset)

        ttk.Label(speed, text="動作延遲：").grid(row=1, column=0, padx=4, pady=2, sticky="w")
        self.dly_act_lo = tk.DoubleVar(value=0.3)
        self.dly_act_hi = tk.DoubleVar(value=0.7)
        self._mk_float_entry(speed, self.dly_act_lo, width=6).grid(row=1, column=1, padx=2)
        ttk.Label(speed, text="~").grid(row=1, column=2)
        self._mk_float_entry(speed, self.dly_act_hi, width=6).grid(row=1, column=3, padx=2)
        ttk.Label(speed, text="（每點 radio/select 後）", foreground="#666").grid(row=1, column=4, padx=8, sticky="w")

        ttk.Label(speed, text="頁面切換延遲：").grid(row=2, column=0, padx=4, pady=2, sticky="w")
        self.dly_page_lo = tk.DoubleVar(value=0.8)
        self.dly_page_hi = tk.DoubleVar(value=1.5)
        self._mk_float_entry(speed, self.dly_page_lo, width=6).grid(row=2, column=1, padx=2)
        ttk.Label(speed, text="~").grid(row=2, column=2)
        self._mk_float_entry(speed, self.dly_page_hi, width=6).grid(row=2, column=3, padx=2)
        ttk.Label(speed, text="（按下一步後）", foreground="#666").grid(row=2, column=4, padx=8, sticky="w")

        ttk.Label(speed, text="每筆間隔：").grid(row=3, column=0, padx=4, pady=2, sticky="w")
        self.dly_btw_lo = tk.DoubleVar(value=1.5)
        self.dly_btw_hi = tk.DoubleVar(value=3.0)
        self._mk_float_entry(speed, self.dly_btw_lo, width=6).grid(row=3, column=1, padx=2)
        ttk.Label(speed, text="~").grid(row=3, column=2)
        self._mk_float_entry(speed, self.dly_btw_hi, width=6).grid(row=3, column=3, padx=2)
        ttk.Label(speed, text="（取完一筆到下一筆開始）", foreground="#666").grid(row=3, column=4, padx=8, sticky="w")

        ttk.Label(speed, text="等待逾時：").grid(row=4, column=0, padx=4, pady=2, sticky="w")
        self.dly_timeout = tk.IntVar(value=30)
        self._mk_int_entry(speed, self.dly_timeout, width=6).grid(row=4, column=1, padx=2)
        ttk.Label(speed, text="秒  （多人連線會慢，建議 30+）", foreground="#666").grid(row=4, column=2, columnspan=3, padx=4, sticky="w")

        # ─ 比例設定區（2 欄並列） ─
        # 左欄：性別 / 國籍 / 教育
        self.gender_pcts  = self._make_pct_block("性別分布", GENDERS, [100, 0, 0, 0],            parent=col_left)
        self.nation_pcts  = self._make_pct_block("國籍分布", NATIONS, [100, 0],                  parent=col_left)
        self.edu_pcts     = self._make_pct_block("教育程度分布", EDUS, [0, 33, 34, 33, 0],       parent=col_left)
        # 右欄：性傾向 / 篩檢習慣 / 18 歲前居住地 / 現居住地
        self.orient_pcts  = self._make_pct_block("性傾向分布", ORIENTS, [0, 0, 100],             parent=col_right)
        self.testing_pcts = self._make_pct_block("篩檢習慣分布（Q1）", TESTING, [50, 50],         parent=col_right)
        self.res18_rows   = self._make_city_block("18 歲以前居住地分布",                          parent=col_right)
        self.resCur_rows  = self._make_city_block("現居住地分布",                                  parent=col_right)

        # ── v1.0.19 單筆取號分頁內容 ──
        self._build_single_tab(tab_single)

        # ─ 控制按鈕 ─
        btn_fr = ttk.Frame(self.root)
        btn_fr.pack(fill="x", padx=6, pady=8)

        # 左側：輔助按鈕 + 路徑說明
        left = ttk.Frame(btn_fr)
        left.pack(side="left", fill="y")
        ttk.Button(left, text="📁 輸出資料夾", command=self.open_outdir).pack(side="left", padx=2)
        ttk.Button(left, text="📋 log 資料夾", command=self.open_logdir).pack(side="left", padx=2)
        ttk.Button(left, text="🐛 debug 資料夾", command=self.open_debugdir).pack(side="left", padx=2)
        ttk.Label(left, text=f"  輸出：{OUTPUT_DIR}", foreground="#666").pack(side="left", padx=8)

        # 右側：大按鈕（開始 / 暫停 / 停止）
        right = ttk.Frame(btn_fr)
        right.pack(side="right")
        big_style = ttk.Style()
        try:
            big_style.configure("Big.TButton",     font=("微軟正黑體", 14, "bold"), padding=(20, 10))
            big_style.configure("BigPause.TButton",font=("微軟正黑體", 14, "bold"), padding=(16, 10), foreground="#e65100")
            big_style.configure("BigStop.TButton", font=("微軟正黑體", 14, "bold"), padding=(16, 10), foreground="#b71c1c")
        except Exception:
            pass
        # v1.0.26：按鈕依當前分頁切換行為
        self.start_btn = ttk.Button(right, text="▶ 開始取號", command=self._smart_start, style="Big.TButton")
        self.start_btn.pack(side="left", padx=6, ipady=4)
        self.pause_btn = ttk.Button(right, text="⏸ 暫停", command=self.toggle_pause,
                                     state="disabled", style="BigPause.TButton")
        self.pause_btn.pack(side="left", padx=6, ipady=4)
        self.stop_btn = ttk.Button(right, text="■ 停止並關閉", command=self.stop,
                                    state="disabled", style="BigStop.TButton")
        self.stop_btn.pack(side="left", padx=6, ipady=4)

        # ─ 進度條（v1.0.19 加高 + % 在條內） ─
        prog_fr = ttk.Frame(self.root)
        prog_fr.pack(fill="x", padx=6, pady=2)
        self.spinner_label = ttk.Label(prog_fr, text="●", font=("微軟正黑體", 13, "bold"), foreground="#90a4ae")
        self.spinner_label.pack(side="left", padx=(0, 6))
        self.progress_var = tk.StringVar(value="待命")
        ttk.Label(prog_fr, textvariable=self.progress_var,
                  font=("微軟正黑體", 11, "bold")).pack(side="left")
        # v1.0.19：自訂 Canvas 進度條（高度 34，中央顯示 %）
        self.pb = CanvasProgressBar(prog_fr, height=34)
        self.pb.pack(side="left", padx=12, fill="x", expand=True)
        # v1.0.19 統計列（置中）
        stat_fr = ttk.Frame(self.root)
        stat_fr.pack(fill="x", padx=6, pady=(0, 4))
        # 左側：自動開啟 Excel checkbox
        self.auto_open_xlsx = tk.BooleanVar(value=True)
        ttk.Checkbutton(stat_fr, text="完成後自動停止並開啟 Excel",
                        variable=self.auto_open_xlsx).pack(side="right", padx=8)
        # 中央：統計
        self.stats_var = tk.StringVar(value="平均 — 秒/筆   |   已用 00m00s   |   剩餘 —   |   預計完成 —")
        self.stats_label = ttk.Label(stat_fr, textvariable=self.stats_var,
                  font=("微軟正黑體", 11, "bold"), foreground="#1565c0", anchor="center")
        self.stats_label.pack(side="left", fill="x", expand=True)

        log_fr = ttk.LabelFrame(self.root, text="執行紀錄（綠=成功 / 黃=注意 / 紅=錯誤）")
        log_fr.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_box = tk.Text(log_fr, height=15, font=("Consolas", 10), bg="#1e1e1e", fg="#dcdcdc",
                               insertbackground="#dcdcdc")
        self.log_box.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        # log 著色 tag
        self.log_box.tag_configure("success", foreground="#4caf50")
        self.log_box.tag_configure("warn",    foreground="#ffb300")
        self.log_box.tag_configure("error",   foreground="#ef5350", font=("Consolas", 10, "bold"))
        self.log_box.tag_configure("info",    foreground="#dcdcdc")
        # log 控制按鈕
        log_btns = ttk.Frame(log_fr)
        log_btns.pack(fill="x", padx=4, pady=4)
        ttk.Button(log_btns, text="🧹 清除 Log",  command=self.clear_log).pack(side="left", padx=2)
        ttk.Button(log_btns, text="💾 儲存 Log",  command=self.save_log).pack(side="left", padx=2)
        ttk.Label(log_btns, text="（儲存 Log 會將目前畫面內容存成 .txt，方便回報修正）",
                  foreground="#666").pack(side="left", padx=12)

    def _make_pct_block(self, title, options, defaults, parent=None):
        fr = ttk.LabelFrame(parent or self.root, text=title + " （調一個，其他自動平衡到 100%）")
        fr.pack(fill="x", padx=6, pady=4)
        vars_ = []
        for i, (op, dv) in enumerate(zip(options, defaults)):
            ttk.Label(fr, text=op, width=14, anchor="w").grid(row=i, column=0, padx=4, pady=2, sticky="w")
            v = tk.IntVar(value=dv)
            self._mk_int_entry(fr, v, width=6, justify="center").grid(row=i, column=1, padx=4)
            ttk.Label(fr, text="%").grid(row=i, column=2)
            cnt = tk.StringVar(value="0 筆")
            ttk.Label(fr, textvariable=cnt, foreground="#1565c0", width=8).grid(row=i, column=3, padx=8)
            vars_.append((op, v, cnt))
        for (op, v, cnt) in vars_:
            v.trace_add("write", lambda *a, _v=v, _block=vars_: self._balance_pct(_block, _v))
        return vars_

    # ── v1.0.19/25 單筆取號分頁 ──
    def _build_single_tab(self, parent):
        # v1.0.25：模式切換（單筆也分簡易/完整）
        self.single_mode_var = tk.StringVar(value="簡易")
        mode_fr = ttk.LabelFrame(parent, text="🎛 單筆取號模式")
        mode_fr.pack(fill="x", padx=6, pady=(8, 4))
        ttk.Radiobutton(mode_fr, text="📊 簡易（8 個基本欄位）", variable=self.single_mode_var,
                        value="簡易", command=self._on_single_mode_change).pack(side="left", padx=10, pady=6)
        ttk.Radiobutton(mode_fr, text="📋 完整（全部 48 欄精準設定）", variable=self.single_mode_var,
                        value="完整", command=self._on_single_mode_change).pack(side="left", padx=10, pady=6)
        # v1.0.26：移除上方獨立按鈕；單筆 / 批次 都用底部「▶ 開始取號」（會依當前分頁切換）
        self.single_result_var = tk.StringVar(value="尚未取號")
        ttk.Label(mode_fr, textvariable=self.single_result_var,
                  font=("微軟正黑體", 12, "bold"), foreground="#1565c0").pack(side="right", padx=10)
        ttk.Label(mode_fr, text="（按下方「▶ 開始取號」啟動）  ",
                  foreground="#666", font=("微軟正黑體", 9, "italic")).pack(side="right")

        self.single_vars = {}

        # v1.0.25：同 key 重用 var → 簡易/完整雙向同步
        def get_or_create_var(key, default, var_class=tk.StringVar):
            if key in self.single_vars:
                return self.single_vars[key]
            v = var_class(value=default)
            self.single_vars[key] = v
            return v

        def mk_radio_row(parent, label, key, options, default):
            fr = ttk.Frame(parent); fr.pack(fill="x", pady=2, padx=6)
            ttk.Label(fr, text=label, width=18, anchor="w").pack(side="left")
            v = get_or_create_var(key, default)
            for op in options:
                ttk.Radiobutton(fr, text=op, variable=v, value=op).pack(side="left", padx=3)

        def mk_combo_row(parent, label, key, values, default, width=22):
            fr = ttk.Frame(parent); fr.pack(fill="x", pady=2, padx=6)
            ttk.Label(fr, text=label, width=18, anchor="w").pack(side="left")
            v = get_or_create_var(key, default)
            ttk.Combobox(fr, textvariable=v, values=values, state="readonly", width=width).pack(side="left", padx=4)

        def mk_year_row(parent, label, key, default):
            fr = ttk.Frame(parent); fr.pack(fill="x", pady=2, padx=6)
            ttk.Label(fr, text=label, width=18, anchor="w").pack(side="left")
            v = get_or_create_var(key, default, var_class=tk.IntVar)
            self._mk_int_entry(fr, v, width=8).pack(side="left", padx=4)
            ttk.Label(fr, text=" 西元年").pack(side="left", padx=4)

        def mk_text_row(parent, label, key, default=""):
            fr = ttk.Frame(parent); fr.pack(fill="x", pady=2, padx=6)
            ttk.Label(fr, text=label, width=18, anchor="w").pack(side="left")
            v = get_or_create_var(key, default)
            ttk.Entry(fr, textvariable=v, width=30).pack(side="left", padx=4)

        # ── 簡易模式 frame ──
        self.single_simple_fr = ttk.Frame(parent)
        s_left  = ttk.LabelFrame(self.single_simple_fr, text="個案資料")
        s_right = ttk.LabelFrame(self.single_simple_fr, text="說明")
        s_left.pack(side="left",  fill="both", expand=True, padx=(6, 3))
        s_right.pack(side="left", fill="both", expand=True, padx=(3, 6))
        mk_radio_row(s_left, "性別",       "gender", GENDERS, "男")
        mk_radio_row(s_left, "國籍",       "nation", NATIONS, "本國籍")
        mk_year_row (s_left, "出生年",     "year",   1990)
        mk_combo_row(s_left, "18歲前居住地","res18",  CITIES, "台南市")
        mk_combo_row(s_left, "現居住地",   "resCur", CITIES, "台南市")
        mk_radio_row(s_left, "性傾向",     "orient", ORIENTS, "異性")
        mk_radio_row(s_left, "教育程度",   "edu",    EDUS,    "高中職")
        mk_radio_row(s_left, "篩檢習慣",   "testing",TESTING, "否")
        ttk.Label(s_right, text="""
簡易模式：
  ‧ 8 個基本欄位
  ‧ Q1-Q13 全部用「否」走最簡單路徑
  ‧ 適合大部分外展協助情境

完整模式：
  ‧ 切到右上「完整」可精準設定全部 48 欄
  ‧ 適合需要 Q6=是 / Q7=是 / PrEP-PEP 等特殊條件
""", justify="left", padding=(10, 10), foreground="#37474f").pack(anchor="w", padx=8, pady=8)

        # ── 完整模式 frame（分頁顯示） ──
        self.single_complete_fr = ttk.Frame(parent)
        # 用內部 Notebook 分 7 頁
        sub_nb = ttk.Notebook(self.single_complete_fr)
        sub_nb.pack(fill="both", expand=True, padx=6, pady=4)

        # P1
        p1 = ttk.Frame(sub_nb); sub_nb.add(p1, text="P1 風險評估")
        mk_radio_row(p1, "Q1 過性行為",      "q1_sex",      ["是", "否"], "否")
        mk_radio_row(p1, "Q2 全程保險套",    "q2_condom",   ["是", "否", "沒有發生"], "沒有發生")
        mk_radio_row(p1, "Q3 跟固定性伴侶",  "q3_regular",  ["是", "否", "沒有發生"], "沒有發生")
        mk_radio_row(p1, "Q4 用酒",          "q4_alcohol",  ["是", "否", "沒有發生"], "沒有發生")
        mk_radio_row(p1, "Q5 用藥",          "q5_drug",     ["是", "否", "沒有發生"], "沒有發生")

        # P2
        p2 = ttk.Frame(sub_nb); sub_nb.add(p2, text="P2 性病")
        mk_radio_row(p2, "Q6 1年內感染性病",     "q6_std",       ["是", "否"], "否")
        mk_radio_row(p2, "Q6.1 HIV (Q6=是才填)", "q6_hiv",       ["是", "否"], "否")
        mk_text_row (p2, "  HIV 想再篩原因",     "q6_hiv_reason")
        mk_radio_row(p2, "Q6.2 菜花",            "q6_warts",     ["是", "否"], "否")
        mk_radio_row(p2, "Q6.3 梅毒",            "q6_syphilis",  ["是", "否"], "否")
        mk_radio_row(p2, "Q6.4 淋病",            "q6_gonorrhea", ["是", "否"], "否")
        mk_radio_row(p2, "Q6.5 披衣菌",          "q6_chlamydia", ["是", "否"], "否")
        mk_radio_row(p2, "Q6.6 疱疹",            "q6_herpes",    ["是", "否"], "否")
        mk_radio_row(p2, "Q6.7 A肝",             "q6_hepA",      ["是", "否"], "否")
        mk_radio_row(p2, "Q6.8 C肝",             "q6_hepC",      ["是", "否"], "否")
        mk_text_row (p2, "Q6.9 其他",            "q6_other")

        # P3
        p3 = ttk.Frame(sub_nb); sub_nb.add(p3, text="P3 藥物")
        mk_radio_row(p3, "Q7 1年內成癮藥物",     "q7_drug_use",  ["是", "否"], "否")
        mk_radio_row(p3, "Q7.1 安非他命 (Q7=是)", "q7_amph",     ["是", "否"], "否")
        mk_text_row (p3, "  使用方式(吸入,注射,口服)", "q7_amph_method")
        mk_radio_row(p3, "Q7.2 G水",             "q7_ghb",       ["是", "否"], "否")
        mk_radio_row(p3, "Q7.3 搖頭丸",          "q7_mdma",      ["是", "否"], "否")
        mk_radio_row(p3, "Q7.4 K他命",           "q7_ketamine",  ["是", "否"], "否")
        mk_radio_row(p3, "Q7.5 RUSH",            "q7_rush",      ["是", "否"], "否")
        mk_radio_row(p3, "Q7.6 喵喵",            "q7_meph",      ["是", "否"], "否")
        mk_radio_row(p3, "Q7.7 海洛因",          "q7_heroin",    ["是", "否"], "否")
        mk_radio_row(p3, "Q7.8 大麻",            "q7_marijuana", ["是", "否"], "否")
        mk_text_row (p3, "Q7.9 其他",            "q7_other")
        mk_radio_row(p3, "Q7.10 目前使用狀態",   "q7_status",    ["還在使用", "已停用"], "")

        # P4
        p4 = ttk.Frame(sub_nb); sub_nb.add(p4, text="P4 性接觸場合")
        mk_radio_row(p4, "Q8a 網路認識",         "q8a_online",      ["是", "否"], "否")
        mk_radio_row(p4, "Q8b 娛樂場所認識",     "q8b_venue",       ["是", "否"], "否")
        mk_radio_row(p4, "Q8c 性交易服務者",     "q8c_sex_worker",  ["是", "否"], "否")
        mk_radio_row(p4, "Q8d 性交易消費者",     "q8d_sex_consumer",["是", "否"], "否")

        # P5
        p5 = ttk.Frame(sub_nb); sub_nb.add(p5, text="P5 PEP/PrEP")
        mk_radio_row(p5, "Q9 固定伴侶HIV",       "q9_partner_hiv",
                      ["是", "否", "不確定", "目前沒有固定性伴侶"], "否")
        mk_radio_row(p5, "Q10 PEP使用過",        "q10_pep_used",   ["是", "否"], "否")
        mk_radio_row(p5, "Q11 PEP想服用",        "q11_pep_want",   ["是", "否"], "否")
        mk_radio_row(p5, "Q12 聽過PrEP",         "q12_prep_heard", ["是", "否"], "否")
        mk_radio_row(p5, "Q13 PrEP想服用",       "q13_prep_want",  ["是", "否"], "否")

        # P6（基本資料）— v1.0.25：直接使用編輯控件，與簡易模式雙向同步（共用同一 StringVar）
        p6 = ttk.Frame(sub_nb); sub_nb.add(p6, text="P6 基本資料")
        ttk.Label(p6, text="✓ 與簡易模式雙向同步 — 在此修改會同步反映到簡易分頁",
                  foreground="#2e7d32", font=("微軟正黑體", 9, "italic")).pack(pady=(6, 4))
        mk_radio_row(p6, "性別",        "gender", GENDERS, "男")
        mk_radio_row(p6, "國籍",        "nation", NATIONS, "本國籍")
        mk_year_row (p6, "出生年",      "year",   1990)
        mk_combo_row(p6, "18歲前居住地","res18",  CITIES, "台南市")
        mk_combo_row(p6, "現居住地",    "resCur", CITIES, "台南市")
        mk_radio_row(p6, "性傾向",      "orient", ORIENTS, "異性")
        mk_radio_row(p6, "教育程度",    "edu",    EDUS,    "高中職")

        # P7
        p7 = ttk.Frame(sub_nb); sub_nb.add(p7, text="P7 篩檢習慣 + 聯絡")
        mk_radio_row(p7, "Q1 篩檢習慣", "testing_habit", ["是", "否", "從未做過"], "否")
        mk_text_row (p7, "手機號碼（選填）", "phone")
        mk_text_row (p7, "E-mail（選填）",   "email")
        mk_text_row (p7, "其它聯絡（選填）", "other_contact")

        # 預設顯示簡易
        self._on_single_mode_change()

    def _on_single_mode_change(self):
        m = self.single_mode_var.get()
        if m == "完整":
            try: self.single_simple_fr.pack_forget()
            except Exception: pass
            self.single_complete_fr.pack(fill="both", expand=True)
        else:
            try: self.single_complete_fr.pack_forget()
            except Exception: pass
            self.single_simple_fr.pack(fill="both", expand=True)

    def _make_city_block(self, title, parent=None):
        fr = ttk.LabelFrame(parent or self.root, text=title + " （調一個，其他自動平衡到 100%）")
        fr.pack(fill="x", padx=6, pady=4)
        rows_holder = ttk.Frame(fr)
        rows_holder.pack(fill="x", padx=4, pady=2)
        rows = []

        def add_row(city="台南市", pct=100):
            row_fr = ttk.Frame(rows_holder)
            row_fr.pack(fill="x", pady=1)
            cv = tk.StringVar(value=city)
            pv = tk.IntVar(value=pct)
            cb = ttk.Combobox(row_fr, textvariable=cv, values=CITIES, width=12, state="readonly")
            cb.pack(side="left", padx=2)
            self._mk_int_entry(row_fr, pv, width=6).pack(side="left", padx=2)
            ttk.Label(row_fr, text="%").pack(side="left")
            def remove():
                row_fr.destroy()
                rows.remove(item)
                # 移除後重新平衡（讓剩下總和為 100）
                if rows and not self._balancing:
                    self._balance_city(rows, rows[0][1])
                self._update_counts()
            ttk.Button(row_fr, text="✕", width=3, command=remove).pack(side="left", padx=4)
            item = (cv, pv, row_fr)
            rows.append(item)
            # 綁 trace
            pv.trace_add("write", lambda *a, _pv=pv, _rows=rows: self._balance_city(_rows, _pv))

        ttk.Button(fr, text="＋ 添加縣市", command=lambda: add_row("台北市", 0)).pack(anchor="w", padx=4, pady=2)
        add_row("台南市", 100)
        return rows

    def _apply_speed_preset(self, *_):
        """切換速度預設模式時自動填入建議值"""
        m = self.speed_preset.get()
        presets = {
            "快（偷懶模式）":   (0.15, 0.4,  0.5, 1.0, 0.8, 1.5, 20),
            "中（推薦）":         (0.3,  0.7,  0.8, 1.5, 1.5, 3.0, 30),
            "慢（高峰時段）":     (0.5,  1.2,  1.5, 2.5, 3.0, 5.0, 45),
            "極慢（嚴重塞車）":   (0.8,  2.0,  2.5, 4.0, 5.0, 8.0, 60),
        }
        if m in presets:
            a_lo, a_hi, p_lo, p_hi, b_lo, b_hi, to = presets[m]
            self.dly_act_lo.set(a_lo); self.dly_act_hi.set(a_hi)
            self.dly_page_lo.set(p_lo); self.dly_page_hi.set(p_hi)
            self.dly_btw_lo.set(b_lo);  self.dly_btw_hi.set(b_hi)
            self.dly_timeout.set(to)

    def _build_delay_config(self):
        return DelayConfig(
            action_lo=self.dly_act_lo.get(),  action_hi=self.dly_act_hi.get(),
            page_lo=self.dly_page_lo.get(),   page_hi=self.dly_page_hi.get(),
            between_lo=self.dly_btw_lo.get(), between_hi=self.dly_btw_hi.get(),
            wait_timeout=self.dly_timeout.get(),
        )

    def _update_counts(self, *_):
        try:
            tot = self.total_var.get()
        except Exception:
            tot = 0
        for block in (self.gender_pcts, self.nation_pcts, self.edu_pcts, self.orient_pcts, self.testing_pcts):
            for op, v, cnt in block:
                try: pv = v.get()
                except Exception: pv = 0
                cnt.set(f"{int(round(tot * pv / 100))} 筆")

    def log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        write_log_line(line)  # 同步寫入 number\log\log_*.txt
        try:
            tag = self._classify_log(msg)
            self.log_box.insert("end", line + "\n", tag)
            self.log_box.see("end")
        except Exception:
            pass

    @staticmethod
    def _classify_log(msg):
        """依符號優先 → 關鍵詞次之 的策略分類 log 顏色。
           注意：詞彙比對需有「動作語氣」才染色，純名詞（如「失敗快照路徑」）不算。"""
        m = str(msg).strip()

        # ── 1. 強符號（決定性） ──
        # 紅
        for sym in ("✗", "✖", "❌"):
            if m.startswith(sym) or sym in m[:6]:
                return "error"
        # 綠
        for sym in ("✓", "✔"):
            if m.startswith(sym) or sym in m[:6]:
                return "success"
        # 黃
        for sym in ("⚠",):
            if sym in m:
                return "warn"

        # ── 2. 帶冒號的動作語氣 ──
        # 紅 — 「失敗：」「錯誤：」「異常：」等開頭明確說「結果是失敗」
        for kw in ("失敗：", "錯誤：", "異常：", "Exception", "Traceback", "ImportError"):
            if kw in m:
                return "error"
        # 綠
        for kw in ("成功：", "完成：", "已存", "已搬", "已開啟", "已推送", "已加入"):
            if kw in m:
                return "success"
        # 黃
        for kw in ("注意：", "警告：", "Warning", "繁忙", "等不到", "略過", "跳過", "請先"):
            if kw in m:
                return "warn"

        # ── 3. 強動作字（句中） ──
        if "💾" in m: return "success"
        if "▶ 開始" in m or "📦 已將" in m: return "success"
        if "■ 已停止" in m or "停止" == m or m.startswith("⏸"): return "warn"

        return "info"

    def status(self, txt):
        try: self.progress_var.set(txt)
        except Exception: pass

    def open_outdir(self):
        ensure_outdir()
        try:
            os.startfile(OUTPUT_DIR)
        except Exception as e:
            messagebox.showerror("開啟失敗", str(e))

    def open_logdir(self):
        ensure_outdir()
        p = os.path.join(OUTPUT_DIR, "log")
        try:
            os.startfile(p)
        except Exception as e:
            messagebox.showerror("開啟失敗", str(e))

    def open_debugdir(self):
        ensure_outdir()
        p = os.path.join(OUTPUT_DIR, "debug")
        try:
            os.startfile(p)
        except Exception as e:
            messagebox.showerror("開啟失敗", str(e))

    def clear_log(self):
        try:
            self.log_box.delete("1.0", "end")
        except Exception:
            pass

    def save_log(self):
        """v1.0.14：點下就直接存到 number/log/，不再問是否開啟"""
        try:
            content = self.log_box.get("1.0", "end-1c")
        except Exception:
            content = ""
        if not content.strip():
            self.log("⚠ 目前 log 是空的，沒有東西可儲存")
            return
        ensure_outdir()
        fp = os.path.join(OUTPUT_DIR, "log",
            f"log_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content)
            self.log(f"💾 已存 Log → {fp}")
        except Exception as e:
            self.log(f"⚠ Log 儲存失敗：{e}")

    # ── 比例正規化驗證 ──
    def _validate_pcts(self, block, name):
        s = sum(v.get() for _, v, _ in block)
        if s != 100:
            messagebox.showerror("比例錯誤", f"{name} 合計 = {s}%，必須 = 100%")
            return False
        return True

    def _validate_city_rows(self, rows, name):
        if not rows:
            messagebox.showerror("比例錯誤", f"{name} 至少需 1 列")
            return False
        s = sum(p.get() for _, p, _ in rows)
        if s != 100:
            messagebox.showerror("比例錯誤", f"{name} 合計 = {s}%，必須 = 100%")
            return False
        return True

    def _build_profile_pool(self):
        """依比例展開成 N 筆 profile 列表（並隨機打亂）"""
        n = self.total_var.get()

        def expand(block):
            opts, ws = [], []
            for op, v, _ in block:
                opts.append(op); ws.append(v.get())
            return opts, ws

        def expand_city(rows):
            opts, ws = [], []
            for cv, pv, _ in rows:
                opts.append(cv.get()); ws.append(pv.get())
            return opts, ws

        g_opts, g_ws = expand(self.gender_pcts)
        n_opts, n_ws = expand(self.nation_pcts)
        e_opts, e_ws = expand(self.edu_pcts)
        o_opts, o_ws = expand(self.orient_pcts)
        t_opts, t_ws = expand(self.testing_pcts)
        r18_opts, r18_ws = expand_city(self.res18_rows)
        rC_opts, rC_ws  = expand_city(self.resCur_rows)

        ylo, yhi = sorted([self.year_lo.get(), self.year_hi.get()])

        pool = []
        for _ in range(n):
            pool.append({
                "gender": weighted_pick(g_opts, g_ws),
                "nation": weighted_pick(n_opts, n_ws),
                "year":   random.randint(ylo, yhi),
                "res18":  weighted_pick(r18_opts, r18_ws),
                "resCur": weighted_pick(rC_opts, rC_ws),
                "orient": weighted_pick(o_opts, o_ws),
                "edu":    weighted_pick(e_opts, e_ws),
                "testing": weighted_pick(t_opts, t_ws),
            })
        return pool

    # ── v1.0.29 增量儲存 Excel：完整 48 欄輸出 + 同日合併（依 header 對應） ──
    def _incremental_save_excel(self):
        if not self.results:
            return
        try:
            from openpyxl import Workbook, load_workbook
            from openpyxl.styles import Font, PatternFill, Alignment
        except Exception as e:
            self.log(f"⚠ openpyxl 載入失敗，無法寫 Excel：{e}")
            return

        def _logical_blank(rec):
            """v1.0.34：parent=否 時清空子題（讓 xlsx 邏輯一致）"""
            r = dict(rec)
            if r.get("q6_std") == "否":
                for k in ("q6_hiv", "q6_hiv_reason", "q6_warts", "q6_syphilis",
                          "q6_gonorrhea", "q6_chlamydia", "q6_herpes",
                          "q6_hepA", "q6_hepC", "q6_other"):
                    r[k] = ""
            if r.get("q7_drug_use") == "否":
                for k in ("q7_amph", "q7_amph_method", "q7_ghb", "q7_mdma",
                          "q7_ketamine", "q7_rush", "q7_meph",
                          "q7_heroin", "q7_marijuana", "q7_other", "q7_status"):
                    r[k] = ""
            # Q6.1 HIV=否/空 → HIV 原因留空
            if r.get("q6_hiv") != "是":
                r["q6_hiv_reason"] = ""
            # 安非他命=否/空 → 使用方式留空
            if r.get("q7_amph") != "是":
                r["q7_amph_method"] = ""
            return r

        def _row_for_record(no, r):
            """組成一列：# / code / ts / 篩檢路徑 / 風險摘要 / 48 欄（先邏輯清空）"""
            r = _logical_blank(r)
            path, risk = _classify_record(r)
            row = [no, r.get("code", ""), r.get("ts", ""), path, risk]
            for k in COMPLETE_KEYS:
                row.append(r.get(k, ""))
            return row

        def _normalize_existing(old_rows, old_headers):
            """v1.0.35：以 header 名對應到新 schema（含摘要欄位置 +2 偏移）"""
            label_to_key = {v: k for k, v in OUTPUT_LABELS.items()}
            label_to_key.update({"性別": "gender", "國籍": "nation", "出生年": "year",
                                  "18歲前居住地": "res18", "現居住地": "resCur",
                                  "性傾向": "orient", "教育程度": "edu", "篩檢習慣": "testing_habit"})
            # 新 schema：[#, 代碼, 取得時間, 篩檢路徑, 風險摘要, ...COMPLETE_KEYS]
            col_to_output_pos = {}
            for i, h in enumerate(old_headers):
                if not h: continue
                hs = str(h).strip()
                if hs == "#":              col_to_output_pos[i] = 0
                elif hs == "諮詢代碼":     col_to_output_pos[i] = 1
                elif hs == "取得時間":     col_to_output_pos[i] = 2
                elif hs == "篩檢路徑":     col_to_output_pos[i] = 3
                elif hs == "風險摘要":     col_to_output_pos[i] = 4
                else:
                    key = label_to_key.get(hs)
                    if key and key in COMPLETE_KEYS:
                        col_to_output_pos[i] = 5 + COMPLETE_KEYS.index(key)
            normalized = []
            for old in old_rows:
                row = ["" for _ in range(len(OUTPUT_HEADERS))]
                for ci, val in enumerate(old):
                    pos = col_to_output_pos.get(ci)
                    if pos is not None:
                        row[pos] = val
                normalized.append(row)
            return normalized

        try:
            ensure_outdir()
            if not self._current_xlsx_path:
                today = datetime.datetime.now().strftime("%Y%m%d")
                same_day = sorted([
                    fn for fn in os.listdir(OUTPUT_DIR)
                    if fn.startswith(f"codes_{today}_") and fn.lower().endswith(".xlsx")
                       and os.path.isfile(os.path.join(OUTPUT_DIR, fn))
                ])
                self._existing_rows = []
                if same_day:
                    target = os.path.join(OUTPUT_DIR, same_day[-1])
                    try:
                        wb_old = load_workbook(target)
                        ws_old = wb_old.active
                        old_all = list(ws_old.iter_rows(values_only=True))
                        if old_all:
                            old_headers = list(old_all[0])
                            old_rows = [r for r in old_all[1:] if any(c not in (None, "") for c in r)]
                            self._existing_rows = _normalize_existing(old_rows, old_headers)
                        self._current_xlsx_path = target
                        self.log(f"📁 偵測到同日舊檔 {same_day[-1]}（{len(self._existing_rows)} 筆），將合併寫入")
                    except Exception as e:
                        self.log(f"⚠ 讀取同日舊檔失敗，改開新檔：{e}")
                        self._existing_rows = []
                        self._current_xlsx_path = None
                if not self._current_xlsx_path:
                    archive_old_outputs(self.log)
                    fname = f"codes_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    self._current_xlsx_path = os.path.join(OUTPUT_DIR, fname)
                    self._existing_rows = []
            wb = Workbook()
            # v1.0.35：主分頁「全部」+ 路徑分頁
            ws = wb.active
            ws.title = "全部"
            ws.append(OUTPUT_HEADERS)
            # 收集每筆內容（既存 + 新）並依路徑分流
            all_rows = []           # [(content, path)]
            row_idx = 0
            # 既存合併：判路徑
            for old in (self._existing_rows or []):
                row_idx += 1
                content = list(old)
                while len(content) < len(OUTPUT_HEADERS):
                    content.append("")
                content[0] = row_idx
                content = content[:len(OUTPUT_HEADERS)]
                # 從 row 內容反推 record dict 來算 path（取需要的 key）
                # 簡化：直接看 content[3] 若有值用之，否則重算
                rec = {COMPLETE_KEYS[i]: content[5 + i] for i in range(len(COMPLETE_KEYS))}
                if not content[3]:
                    p, ri = _classify_record(rec)
                    content[3] = p; content[4] = ri
                path = content[3] or "簡單"
                all_rows.append((content, path))
            # 新筆
            for r in self.results:
                row_idx += 1
                content = _row_for_record(row_idx, r)
                path = content[3] or "簡單"
                all_rows.append((content, path))
            # 寫到主分頁 + 套色
            FILL_BY_PATH = {
                "簡單":   None,
                "性行為": PatternFill("solid", fgColor="FFE0B2"),  # 淡橘
                "感染史": PatternFill("solid", fgColor="FFCDD2"),  # 淡紅
                "藥物史": PatternFill("solid", fgColor="FFCDD2"),  # 淡紅
            }
            for content, path in all_rows:
                ws.append(content)
                row_n = ws.max_row
                fill = FILL_BY_PATH.get(path)
                if fill:
                    for col in range(1, len(OUTPUT_HEADERS) + 1):
                        ws.cell(row=row_n, column=col).fill = fill
            # 標題列樣式
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF", size=10)
                c.fill = PatternFill("solid", fgColor="1565C0")
                c.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions["A"].width = 5
            ws.column_dimensions["B"].width = 12
            ws.column_dimensions["C"].width = 20
            ws.column_dimensions["D"].width = 10
            ws.column_dimensions["E"].width = 8
            for col_idx in range(6, len(OUTPUT_HEADERS) + 1):
                col_letter = ws.cell(1, col_idx).column_letter
                ws.column_dimensions[col_letter].width = 16
            ws.freeze_panes = "F2"  # 凍結 # / 代碼 / 時間 / 路徑 / 摘要 + 標題列

            # v1.0.35 拆 sheet：依路徑各開分頁（同樣 51 欄）
            for path_name in ("簡單", "性行為", "感染史", "藥物史"):
                rows_for_path = [c for c, p in all_rows if p == path_name]
                if not rows_for_path:
                    continue
                ws_p = wb.create_sheet(path_name)
                ws_p.append(OUTPUT_HEADERS)
                # 重編號
                for i, content in enumerate(rows_for_path, 1):
                    cc = list(content); cc[0] = i
                    ws_p.append(cc)
                for c in ws_p[1]:
                    c.font = Font(bold=True, color="FFFFFF", size=10)
                    c.fill = PatternFill("solid", fgColor="1565C0")
                    c.alignment = Alignment(horizontal="center", vertical="center")
                ws_p.column_dimensions["A"].width = 5
                ws_p.column_dimensions["B"].width = 12
                ws_p.column_dimensions["C"].width = 20
                ws_p.column_dimensions["D"].width = 10
                ws_p.column_dimensions["E"].width = 8
                for col_idx in range(6, len(OUTPUT_HEADERS) + 1):
                    col_letter = ws_p.cell(1, col_idx).column_letter
                    ws_p.column_dimensions[col_letter].width = 16
                ws_p.freeze_panes = "F2"
            wb.save(self._current_xlsx_path)
        except Exception as e:
            self.log(f"⚠ 寫 Excel 失敗：{e}")

    # ── 啟動 ──
    # ── v1.0.21 模式切換 + xlsx 匯入匯出 ──
    def _on_mode_change(self):
        is_complete = (self.mode_var.get() == "完整（匯入xlsx）")
        try:
            if is_complete:
                self._batch_topbar.pack_forget()
                self._batch_midbar.pack_forget()
                self._batch_preview_fr.pack(fill="x", padx=6, pady=4)
                self.log("📋 切換到完整模式（請匯入 xlsx）")
            else:
                self._batch_preview_fr.pack_forget()
                self._batch_topbar.pack(fill="x", padx=6, pady=4)
                self._batch_midbar.pack(fill="x", padx=6, pady=4)
                self.log("📊 切換到簡易模式")
        except Exception as e:
            self.log(f"⚠ 模式切換異常：{e}")

    def _export_sample_xlsx(self):
        ensure_outdir()
        fp = os.path.join(OUTPUT_DIR, "sample_complete_mode.xlsx")
        try:
            make_sample_xlsx(fp)
            self.log(f"💾 範例 xlsx 已產生：{fp}")
            try: os.startfile(fp)
            except Exception: pass
        except Exception as e:
            messagebox.showerror("匯出失敗", str(e))

    def _import_xlsx(self):
        from tkinter import filedialog
        fp = filedialog.askopenfilename(
            title="選擇要匯入的 xlsx",
            filetypes=[("Excel 檔", "*.xlsx"), ("所有檔案", "*.*")],
            initialdir=OUTPUT_DIR,
        )
        if not fp: return
        try:
            profiles, warn = import_xlsx_profiles(fp)
            if isinstance(warn, dict) and warn.get("fatal"):
                messagebox.showerror("匯入錯誤", warn["fatal"]); return
            if not profiles:
                messagebox.showwarning("匯入警告", "沒有讀到任何資料列")
                return
            # v1.0.33：發現 blank 或 invalid 時，跳出驗證 modal 讓使用者決定
            blanks = warn.get("blank", []) if isinstance(warn, dict) else []
            invalids = warn.get("invalid", []) if isinstance(warn, dict) else []
            if blanks or invalids:
                go = self._show_validation_modal(fp, profiles, blanks, invalids)
                if not go:
                    self.log(f"📋 已取消匯入（使用者選擇回去修 {os.path.basename(fp)}）")
                    return
            # 通過驗證或使用者選擇繼續
            self._imported_profiles = profiles
            self.import_status_var.set(f"✓ 已匯入 {len(profiles)} 筆")
            self.log(f"📥 已匯入 {len(profiles)} 筆 → {os.path.basename(fp)}")
            for ridx, fields in blanks[:5]:
                self.log(f"  ⚠ 列 {ridx} 未填：{', '.join(fields[:6])}{'...' if len(fields)>6 else ''}（已用預設值）")
            if len(blanks) > 5:
                self.log(f"  ⚠ 另有 {len(blanks)-5} 列有未填欄位")
            for ridx, label, bad, dv in invalids[:5]:
                self.log(f"  ⚠ 列 {ridx} {label}=「{bad}」不合法，已改為「{dv}」")
            if self.mode_var.get() != "完整（匯入xlsx）":
                self.mode_var.set("完整（匯入xlsx）")
                self._on_mode_change()
            self._refresh_preview_tree()
        except Exception as e:
            messagebox.showerror("匯入失敗", str(e))

    def _show_validation_modal(self, file_path, profiles, blanks, invalids):
        """v1.0.33：xlsx 匯入驗證警示 modal，回 True=繼續匯入，False=取消"""
        t = THEMES.get(self.theme_var.get(), THEMES[DEFAULT_THEME])
        modal = tk.Toplevel(self.root)
        modal.title("匯入資料驗證")
        modal.configure(bg=t["bg"])
        modal.geometry("760x560")
        modal.transient(self.root)
        modal.grab_set()
        modal.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 760) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 560) // 2
        modal.geometry(f"+{x}+{y}")
        # 標題
        tk.Label(modal, text="⚠ xlsx 匯入發現問題", bg=t["bg"], fg=t["warn"],
                  font=("微軟正黑體", 16, "bold")).pack(pady=(20, 6))
        tk.Label(modal,
                 text=f"檔案：{os.path.basename(file_path)}　／　共 {len(profiles)} 筆資料\n"
                      f"未填欄位：{len(blanks)} 列　／　無效值：{len(invalids)} 處",
                 bg=t["bg"], fg=t["fg"], font=("微軟正黑體", 11)).pack(pady=4)
        # 詳細列表
        list_fr = ttk.Frame(modal); list_fr.pack(fill="both", expand=True, padx=20, pady=8)
        text = tk.Text(list_fr, bg=t["log_bg"], fg=t["log_fg"], font=("Consolas", 10),
                       wrap="word", height=20)
        text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_fr, orient="vertical", command=text.yview)
        sb.pack(side="right", fill="y")
        text.configure(yscrollcommand=sb.set)
        text.tag_configure("blank", foreground="#ffb74d")
        text.tag_configure("invalid", foreground="#ef5350")
        text.tag_configure("hint", foreground="#90a4ae")
        if blanks:
            text.insert("end", "▼ 未填欄位（將套用預設值）：\n", "blank")
            for ridx, fields in blanks[:80]:
                text.insert("end", f"  列 {ridx}：{', '.join(fields)}\n")
            if len(blanks) > 80:
                text.insert("end", f"  ... 另有 {len(blanks)-80} 列\n", "hint")
            text.insert("end", "\n")
        if invalids:
            text.insert("end", "▼ 無效值（將改為預設）：\n", "invalid")
            for ridx, label, bad, dv in invalids[:80]:
                text.insert("end", f"  列 {ridx} {label}=「{bad}」 → 改為「{dv}」\n")
            if len(invalids) > 80:
                text.insert("end", f"  ... 另有 {len(invalids)-80} 處\n", "hint")
            text.insert("end", "\n")
        text.insert("end",
            "選擇：\n"
            "  ✅ 用預設值繼續：照上面預設值套用後直接執行取號（缺漏處用預設）\n"
            "  📝 取消，去修 xlsx：先回去把空格補齊，再重新匯入\n",
            "hint")
        text.configure(state="disabled")
        # 按鈕
        result = {"go": False}
        btn_fr = tk.Frame(modal, bg=t["bg"])
        btn_fr.pack(pady=14)
        def do_continue():
            result["go"] = True
            modal.destroy()
        def do_cancel():
            result["go"] = False
            modal.destroy()
        tk.Button(btn_fr, text="✅ 用預設值繼續", bg=t["accent"], fg="white",
                  font=("微軟正黑體", 12, "bold"), padx=20, pady=10, bd=0, cursor="hand2",
                  command=do_continue).pack(side="left", padx=10)
        tk.Button(btn_fr, text="📝 取消，去修 xlsx", bg="#90a4ae", fg="white",
                  font=("微軟正黑體", 12, "bold"), padx=20, pady=10, bd=0, cursor="hand2",
                  command=do_cancel).pack(side="left", padx=10)
        modal.wait_window()
        return result["go"]

    def _refresh_preview_tree(self):
        """v1.0.23：把匯入的 profiles 顯示在預覽 Treeview"""
        try:
            tree = self.preview_tree
        except AttributeError:
            return
        for item in tree.get_children():
            tree.delete(item)
        for i, p in enumerate(self._imported_profiles, 1):
            tree.insert("", "end", values=(
                i, p.get("gender", ""), p.get("year", ""),
                p.get("res18", ""), p.get("resCur", ""),
                p.get("orient", ""), p.get("edu", ""),
                p.get("q1_sex", ""), p.get("q6_std", ""),
                p.get("q7_drug_use", ""), p.get("testing_habit", ""),
            ))

    # ── v1.0.21 取號中設定鎖定 ──
    def _set_settings_locked(self, locked):
        """取號開始 → True（全部 disable）；停止後 → False"""
        # 簡單做法：遞迴掃過 root 的所有 ttk.Entry / Spinbox / Combobox / Radiobutton / Checkbutton
        def walk(w):
            try:
                cls = w.winfo_class()
                if cls in ("TEntry", "Entry", "TSpinbox", "Spinbox",
                           "TCombobox", "TRadiobutton", "Radiobutton",
                           "TCheckbutton", "Checkbutton"):
                    # 不鎖控制按鈕（start/stop/pause）— 它們是 TButton
                    state = "disabled" if locked else "!disabled"
                    try: w.state([state])
                    except Exception:
                        try: w.configure(state="disabled" if locked else "normal")
                        except Exception: pass
            except Exception: pass
            for c in w.winfo_children():
                walk(c)
        walk(self.root)

    # ── v1.0.21 續傳機制 ──
    def _save_resume_state(self):
        """停止關閉前保存：未完成的 pool + 已完成 index"""
        try:
            if not self.thread or not self.thread.is_alive():
                return
            state = {
                "version": VERSION,
                "saved_at": datetime.datetime.now().isoformat(),
                "results_count": len(self.results),
                "pool": getattr(self, "_current_pool", []),
                "completed_index": getattr(self, "_completed_index", 0),
                "xlsx_path": self._current_xlsx_path,
                "mode": self.mode_var.get(),
            }
            ensure_outdir()
            with open(os.path.join(OUTPUT_DIR, "resume_state.json"), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass

    def _check_resume(self):
        """啟動時檢查是否有未完成的進度"""
        rs_path = os.path.join(OUTPUT_DIR, "resume_state.json")
        if not os.path.exists(rs_path):
            return
        try:
            with open(rs_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return
        pool = state.get("pool", [])
        done = int(state.get("completed_index", 0))
        if not pool or done >= len(pool):
            try: os.remove(rs_path)
            except Exception: pass
            return
        remaining = len(pool) - done
        # 跳出 modal
        self.root.after(300, lambda: self._show_resume_modal(state, pool, done, remaining, rs_path))

    def _show_resume_modal(self, state, pool, done, remaining, rs_path):
        t = THEMES.get(self.theme_var.get(), THEMES[DEFAULT_THEME])
        modal = tk.Toplevel(self.root)
        modal.title("續傳上次進度？")
        modal.configure(bg=t["bg"])
        modal.geometry("520x300")
        modal.transient(self.root)
        modal.grab_set()
        # 居中
        modal.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 520) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 300) // 2
        modal.geometry(f"+{x}+{y}")
        # 標題
        tk.Label(modal, text="🔄 偵測到上次未完成的取號", bg=t["bg"], fg=t["accent"],
                  font=("微軟正黑體", 16, "bold")).pack(pady=(20, 10))
        info = (f"上次跑到第 {done}/{len(pool)} 筆\n"
                f"剩餘 {remaining} 筆\n"
                f"上次儲存時間：{state.get('saved_at', '?')[:19]}\n"
                f"上次 Excel：{os.path.basename(state.get('xlsx_path') or '—')}")
        tk.Label(modal, text=info, bg=t["bg"], fg=t["fg"],
                  font=("微軟正黑體", 11), justify="center").pack(pady=10)
        btn_fr = tk.Frame(modal, bg=t["bg"])
        btn_fr.pack(pady=20)
        def do_resume():
            modal.destroy()
            self._imported_profiles = pool[done:]  # 剩餘
            self.mode_var.set("完整（匯入xlsx）")
            self._on_mode_change()
            self.import_status_var.set(f"✓ 續傳剩 {remaining} 筆")
            self.log(f"🔄 續傳：將從第 {done+1} 筆開始（剩 {remaining} 筆）")
            try:
                # 接續寫入舊 Excel
                self._current_xlsx_path = state.get("xlsx_path")
            except Exception: pass
            try: os.remove(rs_path)
            except Exception: pass
        def do_fresh():
            modal.destroy()
            try: os.remove(rs_path)
            except Exception: pass
            self.log("🆕 已選擇重新開始（清除上次進度）")
        tk.Button(btn_fr, text=f"📂 續傳剩 {remaining} 筆", bg=t["accent"], fg="white",
                  font=("微軟正黑體", 12, "bold"), padx=20, pady=10,
                  command=do_resume, bd=0, cursor="hand2").pack(side="left", padx=10)
        tk.Button(btn_fr, text="🆕 重新開始", bg="#90a4ae", fg="white",
                  font=("微軟正黑體", 12, "bold"), padx=20, pady=10,
                  command=do_fresh, bd=0, cursor="hand2").pack(side="left", padx=10)

    # ── v1.0.20 數字輸入框（取代 Spinbox 上下箭頭） ──
    def _mk_int_entry(self, parent, var, width=6, justify="center"):
        """整數輸入框：只接受 0-9"""
        def vc(P):
            return P == "" or P.isdigit() or (P[0] == "-" and P[1:].isdigit())
        e = ttk.Entry(parent, textvariable=var, width=width, justify=justify,
                      validate="key", validatecommand=(parent.register(vc), "%P"))
        return e

    def _mk_float_entry(self, parent, var, width=6, justify="center"):
        """小數輸入框：接受 0-9 與 . """
        def vc(P):
            if P == "": return True
            try: float(P); return True
            except Exception: return False
        e = ttk.Entry(parent, textvariable=var, width=width, justify=justify,
                      validate="key", validatecommand=(parent.register(vc), "%P"))
        return e

    # ── v1.0.18 主題切換 + 動畫 ──
    def _apply_theme(self, name):
        t = THEMES.get(name, THEMES[DEFAULT_THEME])
        try:
            self.root.configure(bg=t["bg"])
            style = ttk.Style()
            try: style.theme_use("clam")
            except Exception: pass
            # 通用元件
            style.configure(".", background=t["bg"], foreground=t["fg"])
            style.configure("TFrame", background=t["bg"])
            style.configure("TLabel", background=t["bg"], foreground=t["fg"])
            style.configure("TLabelframe", background=t["bg"], foreground=t["accent"], borderwidth=1)
            style.configure("TLabelframe.Label", background=t["bg"], foreground=t["accent"],
                           font=("微軟正黑體", 10, "bold"))
            style.configure("TButton", background=t["panel"], foreground=t["fg"],
                           padding=(8, 4), borderwidth=1)
            style.map("TButton",
                background=[("active", t["accent"]), ("pressed", t["accent_hover"])],
                foreground=[("active", "white"), ("pressed", "white")])
            # 大按鈕（開始 / 暫停 / 停止）
            style.configure("Big.TButton", background=t["accent"], foreground="white",
                           font=("微軟正黑體", 14, "bold"), padding=(20, 10))
            style.map("Big.TButton",
                background=[("active", t["accent_hover"]), ("disabled", "#90a4ae")])
            style.configure("BigPause.TButton", background=t["warn"], foreground="white",
                           font=("微軟正黑體", 14, "bold"), padding=(16, 10))
            style.map("BigPause.TButton",
                background=[("active", "#bf360c"), ("disabled", "#bdbdbd")])
            style.configure("BigStop.TButton", background=t["error"], foreground="white",
                           font=("微軟正黑體", 14, "bold"), padding=(16, 10))
            style.map("BigStop.TButton",
                background=[("active", "#7f0000"), ("disabled", "#bdbdbd")])
            # Entry / Spinbox / Combobox
            style.configure("TEntry", fieldbackground=t["panel"], foreground=t["fg"], borderwidth=1)
            style.configure("TSpinbox", fieldbackground=t["panel"], foreground=t["fg"])
            style.configure("TCombobox", fieldbackground=t["panel"], foreground=t["fg"])
            # Progressbar (legacy ttk - 雖然主進度條改 Canvas，這裡保留設定以防其他地方用)
            style.configure("TProgressbar", troughcolor=t["panel"], background=t["accent"],
                           borderwidth=0, thickness=18)
            # 自訂 Canvas 進度條
            try: self.pb.configure_theme(t["panel"], t["accent"])
            except Exception: pass
            # Checkbutton
            style.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
            # log box
            try:
                self.log_box.configure(bg=t["log_bg"], fg=t["log_fg"], insertbackground=t["log_fg"])
                self.log_box.tag_configure("success", foreground=t["log_success"])
                self.log_box.tag_configure("warn",    foreground=t["log_warn"])
                self.log_box.tag_configure("error",   foreground=t["log_error"], font=("Consolas", 10, "bold"))
                self.log_box.tag_configure("info",    foreground=t["log_fg"])
            except Exception:
                pass
            self.log(f"🎨 已套用主題：{name}")
        except Exception as e:
            self.log(f"⚠ 主題套用失敗：{e}")

    def _start_spinner(self):
        self._spinner_running = True
        self._tick_spinner()

    def _stop_spinner(self):
        self._spinner_running = False
        try: self.spinner_label.configure(text="●", foreground="#90a4ae")
        except Exception: pass

    def _tick_spinner(self):
        if not self._spinner_running:
            return
        frames = ["⣷", "⣯", "⣟", "⡿", "⢿", "⣻", "⣽", "⣾"]
        try:
            t = THEMES.get(self.theme_var.get(), THEMES[DEFAULT_THEME])
            self.spinner_label.configure(text=frames[self._spinner_idx % len(frames)],
                                          foreground=t["accent"])
        except Exception:
            pass
        self._spinner_idx += 1
        self.root.after(120, self._tick_spinner)

    def _flash_stats(self, color="#4caf50"):
        """成功取得一筆 → 統計列短暫閃爍綠色背景"""
        try:
            orig_bg = self.stats_var
            # 透過 Label 配置直接改 fg 模擬閃爍
            label = None
            for child in self.root.winfo_children():
                # 找 stats label，太繁瑣 — 改用更簡單方法：用 progress label 文字色閃爍
                pass
            # 簡化版：用 spinner_label 暫時亮綠
            t = THEMES.get(self.theme_var.get(), THEMES[DEFAULT_THEME])
            self.spinner_label.configure(foreground=t["success"], text="✓")
            self.root.after(200, lambda: self.spinner_label.configure(text="●") if self._spinner_running else None)
            self.root.after(220, lambda: self._tick_spinner() if self._spinner_running else None)
        except Exception:
            pass

    # ── v1.0.15 輸出路徑控制 ──
    def _pick_output_dir(self):
        from tkinter import filedialog
        cur = self.output_dir_var.get() or DEFAULT_OUTPUT_DIR
        if not os.path.isdir(cur):
            cur = os.path.dirname(cur) if os.path.dirname(cur) else DEFAULT_OUTPUT_DIR
        new = filedialog.askdirectory(title="選擇輸出資料夾", initialdir=cur)
        if new:
            self.output_dir_var.set(os.path.normpath(new))
            self._apply_output_dir()

    def _apply_output_dir(self):
        new_path = (self.output_dir_var.get() or "").strip()
        if not new_path:
            messagebox.showwarning("路徑錯誤", "輸出路徑不能空白"); return
        try:
            set_output_dir(new_path)  # 自動建資料夾 + 子目錄
            self.log(f"📁 輸出資料夾已切換：{new_path}")
            self._save_settings()
        except Exception as e:
            messagebox.showerror("套用失敗", f"無法建立或寫入：\n{new_path}\n\n{e}")

    def _reset_output_dir(self):
        self.output_dir_var.set(DEFAULT_OUTPUT_DIR)
        self._apply_output_dir()

    def _reset_stats(self):
        try:
            self.stats_var.set("平均 — 秒/筆   |   已用 00m00s   |   剩餘 —   |   預計完成 —")
            self.pb.configure_value(0, 100)
        except Exception: pass

    def _build_pool_for_mode(self):
        """v1.0.21：依當前模式產 pool"""
        if self.mode_var.get() == "完整（匯入xlsx）":
            if not self._imported_profiles:
                messagebox.showerror("無資料", "完整模式請先匯入 xlsx")
                return None
            return list(self._imported_profiles)
        else:
            return self._build_profile_pool()

    def _smart_start(self):
        """v1.0.26：依當前分頁智慧分派 — 批次→start()；單筆→start_single()"""
        try:
            cur = self.notebook.index(self.notebook.select())
        except Exception:
            cur = 0
        if cur == 1:
            self.start_single()
        else:
            self.start()
        # 依分頁更新按鈕文字
        self._update_start_btn_label()

    def _update_start_btn_label(self):
        """依當前分頁更新「▶ 開始」按鈕文字"""
        try:
            cur = self.notebook.index(self.notebook.select())
            if cur == 1:
                self.start_btn.configure(text="▶ 開始單筆取號")
            else:
                self.start_btn.configure(text="▶ 開始批次取號")
        except Exception:
            pass

    def start(self):
        # 驗證
        for blk, name in [
            (self.gender_pcts, "性別"),
            (self.nation_pcts, "國籍"),
            (self.edu_pcts, "教育程度"),
            (self.orient_pcts, "性傾向"),
            (self.testing_pcts, "篩檢習慣"),
        ]:
            if not self._validate_pcts(blk, name):
                return
        if not self._validate_city_rows(self.res18_rows, "18歲前居住地"): return
        if not self._validate_city_rows(self.resCur_rows, "現居住地"): return
        if self.total_var.get() < 1:
            messagebox.showerror("錯誤", "總筆數至少 1")
            return

        ensure_outdir()
        self._save_settings()
        pool = self._build_pool_for_mode()
        if pool is None:
            return
        self.results = []
        # v1.0.21 續傳時保留 xlsx 路徑
        if not self._current_xlsx_path:
            self._current_xlsx_path = None
        self._current_pool = pool
        self._completed_index = 0
        self.stop_evt.clear()
        self.pause_evt.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.pause_btn.config(state="normal", text="⏸ 暫停")
        self.pb.configure_value(0, len(pool))
        self._reset_stats()
        self._start_spinner()
        self._set_settings_locked(True)  # v1.0.21 反白
        self.thread = threading.Thread(target=self._run, args=(pool,), daemon=True)
        self.thread.start()

    # ── v1.0.19/25 單筆取號 ──
    def start_single(self):
        if self.thread and self.thread.is_alive():
            messagebox.showwarning("執行中", "目前批次取號還在跑，請先停止再用單筆模式")
            return
        ensure_outdir()
        self._save_settings()
        sv = self.single_vars
        is_complete_single = (self.single_mode_var.get() == "完整")
        if is_complete_single:
            # v1.0.25 完整模式：收齊所有 48 欄
            prof = {}
            for k, _, dv, _ in COMPLETE_FIELDS:
                if k in sv:
                    val = sv[k].get()
                    prof[k] = val if val != "" else dv
                else:
                    prof[k] = dv
            try: prof["year"] = int(prof.get("year") or 1990)
            except Exception: prof["year"] = 1990
            # 兼容舊 worker 路徑（is_complete 偵測用 q1_sex 存在）
        else:
            prof = {
                "gender":  sv["gender"].get(),
                "nation":  sv["nation"].get(),
                "year":    int(sv["year"].get()),
                "res18":   sv["res18"].get(),
                "resCur":  sv["resCur"].get(),
                "orient":  sv["orient"].get(),
                "edu":     sv["edu"].get(),
                "testing": sv["testing"].get(),
            }
        self.results = []
        self._current_xlsx_path = None
        self.stop_evt.clear()
        self.pause_evt.clear()
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.pb.configure_value(0, 1)
        self._reset_stats()
        self._start_spinner()
        self.single_result_var.set("執行中…")
        self.thread = threading.Thread(target=self._run, args=([prof],), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_evt.set()
        # 萬一還在暫停狀態，先解除暫停讓主迴圈跑完當前 → 進結束流程
        if self.pause_evt.is_set():
            self.pause_evt.clear()
        self.log("■ 收到停止訊號，等本筆結束 → 存檔 → 自動關閉…")
        try:
            self.stop_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
        except Exception:
            pass

    def toggle_pause(self):
        """暫停/繼續：每筆任務開頭/中段都會檢查 pause_evt，被 set 即進入忙等迴圈"""
        if self.pause_evt.is_set():
            self.pause_evt.clear()
            self.pause_btn.config(text="⏸ 暫停")
            self.log("▶ 已繼續")
        else:
            self.pause_evt.set()
            self.pause_btn.config(text="▶ 繼續")
            self.log("⏸ 已暫停（按「繼續」恢復）")

    # ── v1.0.12 設定持久化 ──
    def _collect_settings(self):
        d = {
            "output_dir": self.output_dir_var.get(),  # v1.0.15
            "theme": self.theme_var.get(),  # v1.0.18
            "total": self.total_var.get(),
            "year_lo": self.year_lo.get(),
            "year_hi": self.year_hi.get(),
            "speed_preset": self.speed_preset.get(),
            "dly_act_lo":  self.dly_act_lo.get(),  "dly_act_hi":  self.dly_act_hi.get(),
            "dly_page_lo": self.dly_page_lo.get(), "dly_page_hi": self.dly_page_hi.get(),
            "dly_btw_lo":  self.dly_btw_lo.get(),  "dly_btw_hi":  self.dly_btw_hi.get(),
            "dly_timeout": self.dly_timeout.get(),
            "gender":  [(op, v.get()) for op, v, _ in self.gender_pcts],
            "nation":  [(op, v.get()) for op, v, _ in self.nation_pcts],
            "edu":     [(op, v.get()) for op, v, _ in self.edu_pcts],
            "orient":  [(op, v.get()) for op, v, _ in self.orient_pcts],
            "testing": [(op, v.get()) for op, v, _ in self.testing_pcts],
            "res18":   [(cv.get(), pv.get()) for cv, pv, _ in self.res18_rows],
            "resCur":  [(cv.get(), pv.get()) for cv, pv, _ in self.resCur_rows],
        }
        return d

    def _save_settings(self):
        try:
            ensure_outdir()
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._collect_settings(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"⚠ 設定存檔失敗：{e}")

    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            self.log(f"⚠ 設定讀取失敗：{e}")
            return
        # 暫停 trace 避免 set 過程被 _balance 干擾
        self._balancing = True
        try:
            # v1.0.15 輸出路徑（先還原才能讓後續存檔走正確位置）
            if "output_dir" in d and d["output_dir"]:
                try:
                    self.output_dir_var.set(d["output_dir"])
                    set_output_dir(d["output_dir"])
                except Exception as e:
                    self.log(f"⚠ 還原輸出路徑失敗：{e}")
            # v1.0.18 主題
            if "theme" in d and d["theme"] in THEMES:
                self.theme_var.set(d["theme"])
                self._apply_theme(d["theme"])
            if "total" in d:    self.total_var.set(d["total"])
            if "year_lo" in d:  self.year_lo.set(d["year_lo"])
            if "year_hi" in d:  self.year_hi.set(d["year_hi"])
            if "speed_preset" in d: self.speed_preset.set(d["speed_preset"])
            for k_ui, k_dict in [
                ("dly_act_lo","dly_act_lo"), ("dly_act_hi","dly_act_hi"),
                ("dly_page_lo","dly_page_lo"), ("dly_page_hi","dly_page_hi"),
                ("dly_btw_lo","dly_btw_lo"), ("dly_btw_hi","dly_btw_hi"),
                ("dly_timeout","dly_timeout"),
            ]:
                if k_dict in d: getattr(self, k_ui).set(d[k_dict])
            def _restore_pct(saved, block):
                if not saved: return
                m = dict(saved)
                for op, v, _ in block:
                    if op in m: v.set(m[op])
            _restore_pct(d.get("gender"),  self.gender_pcts)
            _restore_pct(d.get("nation"),  self.nation_pcts)
            _restore_pct(d.get("edu"),     self.edu_pcts)
            _restore_pct(d.get("orient"),  self.orient_pcts)
            _restore_pct(d.get("testing"), self.testing_pcts)
            # 居住地：移除預設那行 + 從 settings 重建
            def _restore_city(saved, rows, add_row_cb):
                if not saved: return
                # 清空現有列
                for cv, pv, fr in list(rows):
                    fr.destroy()
                rows.clear()
                for city, pct in saved:
                    add_row_cb(city, int(pct))
            # 取出對應的 add_row 函式（reflect from frame）— 我們需先儲存 add_row callback
            # 用替代法：直接設定第一列、加入剩餘列
            if d.get("res18"):
                # 簡化：直接把 row[0] 改值，其餘呼叫該 block 既有的 +添加
                pass  # 預設行為夠用（首次有預設 1 列台南市 100%）
        finally:
            self._balancing = False
        self._update_counts()
        self.log("📁 已載入上次設定")

    def _on_close(self):
        try:
            self._save_settings()
        except Exception:
            pass
        self.root.destroy()

    def _run(self, pool):
        self.log(f"▶ 開始批次取號，共 {len(pool)} 筆")
        dly = self._build_delay_config()
        self.log(f"   延遲設定：動作 {dly.action_lo}~{dly.action_hi}s / 頁面 {dly.page_lo}~{dly.page_hi}s "
                 f"/ 每筆間隔 {dly.between_lo}~{dly.between_hi}s / 逾時 {dly.wait_timeout}s")
        # v1.0.14 統計起始時間
        run_start_ts = time.time()
        completed = 0
        worker = HivaWorker(self.log, self.status, lambda code: None, self.stop_evt, delay_cfg=dly)
        if not worker.start_browser():
            self._finish()
            return
        try:
            for i, prof in enumerate(pool, 1):
                # v1.0.12 暫停檢查（在每筆開頭）
                while self.pause_evt.is_set() and not self.stop_evt.is_set():
                    time.sleep(0.5)
                if self.stop_evt.is_set():
                    self.log("■ 已停止")
                    break
                self.status(f"取第 {i}/{len(pool)} 筆 — {prof['gender']}/{prof['year']}/{prof['edu']}")
                code = worker.fetch_one(prof)
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if code:
                    self.log(f"✓ 第 {i} 筆 → {code}  ({prof['gender']} {prof['year']} {prof['edu']})")
                    # v1.0.29：全欄位記錄（簡易模式以預設值補齊）
                    record = {"no": i, "code": code, "ts": ts}
                    for k, _, dv, _ in COMPLETE_FIELDS:
                        v = prof.get(k)
                        if v is None or v == "":
                            v = dv
                        record[k] = v
                    # 簡易 profile 的 testing 欄位 → 對應到 testing_habit
                    if "testing" in prof and (record.get("testing_habit") in (None, "", "否")):
                        record["testing_habit"] = prof["testing"]
                    self.results.append(record)
                    # v1.0.12 增量儲存 — 每筆完成立刻寫 Excel
                    self._incremental_save_excel()
                    self._flash_stats()  # v1.0.18 成功動畫
                else:
                    self.log(f"✗ 第 {i} 筆 失敗，跳過")
                self.pb.configure_value(i, len(pool))
                # v1.0.14 統計：依完成筆數計算平均 & ETA
                completed += 1
                elapsed = time.time() - run_start_ts
                avg = elapsed / completed if completed else 0
                total = len(pool)
                remaining = total - i
                eta_sec = int(avg * remaining)
                pct = (i / total * 100) if total else 0
                def _fmt(sec):
                    sec = max(0, int(sec))
                    h, r = divmod(sec, 3600); m, s = divmod(r, 60)
                    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
                # v1.0.16 預計完成時間（絕對時間 HH:MM）
                eta_at = (datetime.datetime.now() +
                          datetime.timedelta(seconds=eta_sec)).strftime("%H:%M:%S")
                # v1.0.19 完成%已在進度條內，這列只剩平均/已用/剩餘/預計完成
                self.stats_var.set(
                    f"平均 {avg:.1f} 秒/筆   |   已用 {_fmt(elapsed)}   |   "
                    f"剩餘 {_fmt(eta_sec)}   |   預計完成 {eta_at}")
                # v1.0.19 同步單筆模式結果顯示
                if len(pool) == 1 and code:
                    try: self.single_result_var.set(f"✓ 取到代碼：{code}")
                    except Exception: pass
                self.root.update_idletasks()
                if i < len(pool):
                    # 暫停期間不消耗 between 延遲（先等暫停解除）
                    while self.pause_evt.is_set() and not self.stop_evt.is_set():
                        time.sleep(0.5)
                    if not self.stop_evt.is_set():
                        dly.between()
        finally:
            worker.quit()
            self._save_excel()
            # v1.0.14 全部跑完（非中途停止）且勾選自動開啟 Excel
            done_naturally = not self.stop_evt.is_set()
            if done_naturally and self.auto_open_xlsx.get() and self._current_xlsx_path \
                    and os.path.exists(self._current_xlsx_path):
                self.log("✓ 全部完成，自動開啟 Excel…")
                try: os.startfile(self._current_xlsx_path)
                except Exception as e: self.log(f"⚠ 自動開啟失敗：{e}")
            # v1.0.12 停止鍵 → 存完自動關閉程式
            if self.stop_evt.is_set():
                self.log("✓ 已關閉瀏覽器、存檔完成 → 程式即將自動關閉")
                # v1.0.21 寫入 resume_state（給下次啟動續傳）
                try:
                    rs_state = {
                        "version": VERSION,
                        "saved_at": datetime.datetime.now().isoformat(),
                        "pool": self._current_pool,
                        "completed_index": self._completed_index,
                        "xlsx_path": self._current_xlsx_path,
                        "mode": self.mode_var.get(),
                    }
                    with open(os.path.join(OUTPUT_DIR, "resume_state.json"), "w", encoding="utf-8") as f:
                        json.dump(rs_state, f, ensure_ascii=False, indent=2, default=str)
                    self.log(f"💾 已寫入續傳檔（剩 {len(self._current_pool) - self._completed_index} 筆）")
                except Exception as e:
                    self.log(f"⚠ 續傳檔寫入失敗：{e}")
                try: self._save_settings()
                except Exception: pass
                self.root.after(800, self._force_exit)
            else:
                self._finish()
                # v1.0.14 自然完成 + 勾選 → 同樣自動關閉程式（如使用者勾「完成後自動停止」）
                if done_naturally and self.auto_open_xlsx.get():
                    self.log("✓ 任務結束（已開 Excel），程式 3 秒後關閉…")
                    self.root.after(3000, self._force_exit)

    def _save_excel(self):
        """結束時最終確認儲存（增量儲存可能已經寫好了，這裡再確認一次）"""
        if not self.results:
            self.log("（無資料可存）")
            return
        try:
            self._incremental_save_excel()
            if self._current_xlsx_path and os.path.exists(self._current_xlsx_path):
                self.log(f"💾 已存 Excel → {self._current_xlsx_path}（共 {len(self.results)} 筆）")
            else:
                self.log("⚠ Excel 路徑異常，請手動檢查 number 資料夾")
        except Exception as e:
            self.log(f"⚠ 最終儲存失敗：{e}")

    def _force_exit(self):
        """v1.0.12：停止後完全退出進程（避免 Tk thread 殘留）"""
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def _finish(self):
        self.status(f"完成 — 成功 {len(self.results)} 筆")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled", text="⏸ 暫停")
        self._stop_spinner()
        self._set_settings_locked(False)  # v1.0.21 解鎖
        try:
            if "尚未取號" in self.single_result_var.get() or "執行中" in self.single_result_var.get():
                if not self.results:
                    self.single_result_var.set("✗ 取號失敗，請看 log")
        except Exception:
            pass


def main():
    log_path = init_logfile()
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    app = App(root)
    app._update_counts()
    app.log(f"📁 log 寫入：{log_path}")
    app.log(f"📁 輸出資料夾：{OUTPUT_DIR}")
    app.log(f"   舊版自動歸檔：{os.path.join(OUTPUT_DIR, 'old')}")
    app.log(f"   失敗快照：{os.path.join(OUTPUT_DIR, 'debug')}")
    root.mainloop()


if __name__ == "__main__":
    main()
