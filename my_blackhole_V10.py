import base64
import datetime
import os
from typing import Optional, Tuple, List, Union

import requests
import streamlit as st
import html as _html
import json as _json
from string import Template
from streamlit.components.v1 import html as st_html

APP_TITLE = "My Blackhole — GitHub Cloud Web-Hard"

# =============================
# GitHub helpers
# =============================

def gh_headers(token: str):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gh_api_base(owner: str, repo: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}"

def gh_raw_base(owner: str, repo: str, branch: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"

def request_kwargs():
    ca_path = st.secrets.get("CA_BUNDLE_PATH", "")
    verify = ca_path if ca_path else False  # 내부망 테스트 외엔 CA_BUNDLE_PATH 권장
    http_proxy  = st.secrets.get("HTTP_PROXY", "")
    https_proxy = st.secrets.get("HTTPS_PROXY", "")
    no_proxy    = st.secrets.get("NO_PROXY", "")
    proxies = None
    if http_proxy or https_proxy:
        proxies = {}
        if http_proxy:  proxies["http"] = http_proxy
        if https_proxy: proxies["https"] = https_proxy
    if http_proxy:  os.environ["HTTP_PROXY"]  = http_proxy
    if https_proxy: os.environ["HTTPS_PROXY"] = https_proxy
    if no_proxy:    os.environ["NO_PROXY"]    = no_proxy
    return {"verify": verify, "proxies": proxies}

def path_join(*parts: str) -> str:
    clean = [str(p).strip().strip("/") for p in parts if str(p).strip()]
    return "/".join(clean)

def ensure_folder_path(path: str) -> str:
    return path.strip().strip("/")

def get_file_sha_if_exists(owner: str, repo: str, branch: str, path: str, token: str) -> Tuple[Optional[str], Optional[dict]]:
    url = f"{gh_api_base(owner, repo)}/contents/{path}"
    r = requests.get(url, params={"ref": branch}, headers=gh_headers(token), timeout=30, **request_kwargs())
    if r.status_code == 200:
        data = r.json()
        return data.get("sha"), data
    elif r.status_code == 404:
        return None, None
    else:
        raise RuntimeError(f"GitHub GET contents failed: {r.status_code} {r.text}")

def put_file(owner: str, repo: str, branch: str, path: str, content_bytes: bytes, token: str, message: str, sha: Optional[str] = None) -> dict:
    url = f"{gh_api_base(owner, repo)}/contents/{path}"
    payload = {"message": message, "content": base64.b64encode(content_bytes).decode("utf-8"), "branch": branch}
    if sha: payload["sha"] = sha
    r = requests.put(url, headers=gh_headers(token), json=payload, timeout=60, **request_kwargs())
    if r.status_code in (200, 201):
        return r.json()
    else:
        raise RuntimeError(f"GitHub PUT failed: {r.status_code} {r.text}")

def list_folder(owner: str, repo: str, branch: str, folder: str, token: str) -> List[dict]:
    url = f"{gh_api_base(owner, repo)}/contents/{folder}" if folder else f"{gh_api_base(owner, repo)}/contents"
    r = requests.get(url, params={"ref": branch}, headers=gh_headers(token), timeout=30, **request_kwargs())
    if r.status_code == 200:
        data = r.json()
        return data if isinstance(data, list) else [data]
    elif r.status_code == 404:
        return []
    else:
        raise RuntimeError(f"GitHub list folder failed: {r.status_code} {r.text}")

def delete_file(owner: str, repo: str, branch: str, path: str, token: str, message: str) -> dict:
    sha, _ = get_file_sha_if_exists(owner, repo, branch, path, token)
    if not sha:
        return {"status": "not_found"}
    url = f"{gh_api_base(owner, repo)}/contents/{path}"
    payload = {"message": message, "sha": sha, "branch": branch}
    r = requests.delete(url, headers=gh_headers(token), json=payload, timeout=30, **request_kwargs())
    if r.status_code == 200:
        return r.json()
    else:
        raise RuntimeError(f"GitHub DELETE failed: {r.status_code} {r.text}")

def get_raw_file_bytes(owner: str, repo: str, branch: str, path: str, token: str, sha: Optional[str] = None) -> bytes:
    headers = gh_headers(token).copy()
    headers["Accept"] = "application/vnd.github.raw"
    url = f"{gh_api_base(owner, repo)}/contents/{path}"
    r = requests.get(url, params={"ref": branch}, headers=headers, timeout=60, **request_kwargs())
    if r.status_code == 200:
        return r.content
    if sha:
        headers2 = gh_headers(token).copy()
        headers2["Accept"] = "application/vnd.github.raw"
        url2 = f"{gh_api_base(owner, repo)}/git/blobs/{sha}"
        r2 = requests.get(url2, headers=headers2, timeout=60, **request_kwargs())
        if r2.status_code == 200:
            return r2.content
    raise RuntimeError(f"GitHub download failed: {r.status_code} {r.text}")

def repo_is_private(owner: str, repo: str, token: str) -> bool:
    cache_key = f"{owner}/{repo}"
    if "_repo_visibility_cache" not in st.session_state:
        st.session_state._repo_visibility_cache = {}
    cache = st.session_state._repo_visibility_cache
    if cache_key in cache:
        return cache[cache_key]
    url = gh_api_base(owner, repo)
    r = requests.get(url, headers=gh_headers(token), timeout=30, **request_kwargs())
    if r.status_code == 200:
        priv = bool(r.json().get("private", False))
        cache[cache_key] = priv
        return priv
    cache[cache_key] = True
    return True

def build_data_uri(content_bytes: bytes) -> str:
    b64 = base64.b64encode(content_bytes).decode("ascii")
    return f"data:application/octet-stream;base64,{b64}"

# =============================
# Utils
# =============================

SnippetItem = Union[str, dict]

def _b64decode_any(s: str) -> bytes:
    s = s.strip()
    pad = '=' * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s + pad)
    except Exception:
        return base64.b64decode(s + pad)

# =============================
# Snippet helpers (③ Text Snippet)
# =============================

def _normalize_snippet_item(x: SnippetItem) -> dict:
    if isinstance(x, dict):
        t = str(x.get("t", ""))
        hint = x.get("hint")
        obj = {"t": t}
        if hint:
            obj["hint"] = str(hint)
        return obj
    return {"t": str(x)}

def load_snippets(owner: str, repo: str, branch: str, path: str, token: str) -> Tuple[List[dict], Optional[str]]:
    sha, info = get_file_sha_if_exists(owner, repo, branch, path, token)
    if info and info.get("content"):
        try:
            decoded = base64.b64decode(info["content"]).decode("utf-8", errors="replace")
            data = _json.loads(decoded)
            if isinstance(data, list):
                return [_normalize_snippet_item(x) for x in data], sha
        except Exception:
            pass
    return [], sha

def save_snippets(owner: str, repo: str, branch: str, path: str, token: str, snippets: List[dict], sha: Optional[str]) -> Optional[str]:
    body = _json.dumps([_normalize_snippet_item(x) for x in snippets], ensure_ascii=False, indent=2).encode("utf-8")
    resp = put_file(owner, repo, branch, path, body, token, "Update snippets", sha)
    try:
        return (resp.get("content") or {}).get("sha")
    except Exception:
        return None

# =============================
# UI shell
# =============================

st.set_page_config(page_title=APP_TITLE, page_icon="🕳️", layout="wide")

st.markdown(
    """
    <style>
      header[data-testid="stHeader"], div[data-testid="stToolbar"], footer { display:none !important; }
      html, body, .stApp { margin:0 !important; padding:0 !important; }
      .stMainBlockContainer, .block-container, section.main > div {
        padding-top: .2rem !important;
        padding-bottom: .6rem !important;
      }
      div[data-testid="stTabs"] { margin-top: 0 !important; }
      div[data-testid="stTabs"] > div > div { gap: .5rem !important; }
      .stContainer, .stMarkdown, .stDataFrame { border-radius: 14px; }
      .stApp iframe { width: 100% !important; min-width: 100% !important; display: block !important; }
      .stApp div:has(> iframe) { width: 100% !important; }

      /* 좌/우 라벨 통일 */
      .section-label{
        display:flex; align-items:center; gap:.4rem;
        font-weight:600; font-size:.95rem; line-height:1.15;
        margin: 0 0 .35rem 2px;
      }
      .section-label .ico{ font-size:1.05rem; line-height:1; }

      /* 드롭존 */
      div[data-testid="stFileDropzone"] { min-height: 160px; }
      div[data-testid="stFileUploader"] section[tabindex="0"] { min-height: 160px; padding: .9rem .9rem; }
      section[data-testid="stFileUploadDropzone"] { min-height: 160px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================
# State & Settings
# =============================

def _init_state():
    ss = st.session_state
    if "gh_owner" not in ss: ss.gh_owner = st.secrets.get("GH_OWNER", "")
    if "gh_repo" not in ss: ss.gh_repo = st.secrets.get("GH_REPO", "")
    if "gh_branch" not in ss: ss.gh_branch = st.secrets.get("GH_BRANCH", "main")
    if "folder" not in ss:
        default_folder = st.secrets.get(
            "DEFAULT_FOLDER",
            path_join("my-blackhole", st.secrets.get("DEVICE", "desktop"))
        )
        ss.folder = ensure_folder_path(default_folder)
    if "memo_area" not in ss: ss.memo_area = ""
    if "uploader_key" not in ss: ss.uploader_key = 0

    if "memo_folder" not in ss:
        ss.memo_folder = ensure_folder_path(
            st.secrets.get("MEMO_FOLDER", path_join("my-blackhole", "_memo"))
        )
    if "snippet_folder" not in ss:
        ss.snippet_folder = ensure_folder_path(
            st.secrets.get("SNIPPET_FOLDER", path_join("my-blackhole", "_snippets"))
        )
    if "inline_dl_limit_mb" not in ss:
        ss.inline_dl_limit_mb = float(st.secrets.get("INLINE_DL_LIMIT_MB", 10))

    if "_snippets_loaded" not in ss: ss._snippets_loaded = False
    if "snippets" not in ss: ss.snippets = []
    if "_snip_sha" not in ss: ss._snip_sha = None
    if "_snip_clear" not in ss: ss._snip_clear = False

    if "_memo_autoloaded" not in ss: ss._memo_autoloaded = False

_init_state()

def _settings_panel():
    with st.container(border=True):
        st.subheader("설정")
        st.caption("GitHub 리포와 저장 폴더를 지정합니다. 토큰은 secrets.toml에서만 읽습니다.")
        c1, c2 = st.columns(2)

        with c1:
            st.text_input("Owner (사용자명/조직명)", key="gh_owner", value=st.session_state.gh_owner)
            st.text_input("Repository", key="gh_repo", value=st.session_state.gh_repo)
            st.text_input("메모 폴더 (repo 내 경로)", key="memo_folder", value=st.session_state.memo_folder,
                          help="예: my_blackhole/_memo")
        with c2:
            st.text_input("Branch", key="gh_branch", value=st.session_state.gh_branch)
            st.text_input("저장 폴더 (repo 내 경로)", key="folder", value=st.session_state.folder,
                          help="업로드 기본 대상 폴더. 예: my_blackhole/inbox")
            st.number_input("작은 파일 인라인 다운로드 한도(MB)", key="inline_dl_limit_mb",
                            min_value=1.0, max_value=100.0, step=0.5, value=float(st.session_state.inline_dl_limit_mb),
                            help="이 크기 이하의 파일은 Data URL로 즉시 다운로드합니다.")

        st.text_input("스니펫 폴더 (repo 내 경로)", key="snippet_folder", value=st.session_state.snippet_folder,
                      help="예: my_blackhole/_snippets")

        st.markdown("---")
        if st.secrets.get("GH_TOKEN", ""):
            st.success("GitHub 토큰 감지됨 (secrets.toml)")
        else:
            st.error("GH_TOKEN이 없습니다.")

        if st.button("저장"):
            st.toast("설정을 저장했습니다.")
            st.rerun()

owner  = st.session_state.gh_owner
repo   = st.session_state.gh_repo
branch = st.session_state.gh_branch
folder = st.session_state.folder

token = st.secrets.get("GH_TOKEN", "")
ready = bool(token and owner and repo and branch)

# =============================
# URL query handlers (파일 삭제 + 스니펫 삭제/정렬)
# =============================
try:
    qs = st.query_params

    enc_val = qs.get("del", None)
    if enc_val and ready:
        enc = enc_val[0] if isinstance(enc_val, list) else enc_val
        if enc:
            try:
                rel_path = base64.urlsafe_b64decode(enc.encode("ascii")).decode("utf-8")
                delete_file(owner, repo, branch, rel_path, token, "Delete via My Blackhole")
                st.toast(f"삭제됨: {os.path.basename(rel_path)}")
            except Exception as e:
                st.error(f"삭제 실패: {e}")
            finally:
                try:
                    if "del" in st.query_params: del st.query_params["del"]
                    if "ts"  in st.query_params: del st.query_params["ts"]
                except Exception:
                    pass
                st.rerun()

    del_snip = qs.get("snip_del", None)
    if del_snip and ready:
        idx_str = del_snip[0] if isinstance(del_snip, list) else del_snip
        try:
            idx = int(idx_str)
            snippet_folder = ensure_folder_path(st.session_state.snippet_folder)
            snippet_path = path_join(snippet_folder, "snippets.json")
            current, sha = load_snippets(owner, repo, branch, snippet_path, token)
            if 0 <= idx < len(current):
                current.pop(idx)
                new_sha = save_snippets(owner, repo, branch, snippet_path, token, current, sha)
                st.session_state.snippets = current
                st.session_state._snip_sha = new_sha
            st.toast("스니펫 삭제됨")
        except Exception as e:
            st.error(f"스니펫 삭제 실패: {e}")
        finally:
            try:
                if "snip_del" in st.query_params: del st.query_params["snip_del"]
                if "ts" in st.query_params: del st.query_params["ts"]
            except Exception:
                pass
            st.rerun()

    snip_reorder = qs.get("snip_reorder", None)
    if snip_reorder and ready:
        payload = snip_reorder[0] if isinstance(snip_reorder, list) else snip_reorder
        try:
            raw = _b64decode_any(payload).decode("utf-8", errors="replace")
            arr = _json.loads(raw)
            if isinstance(arr, list):
                normalized = [_normalize_snippet_item(x) for x in arr]
                snippet_folder = ensure_folder_path(st.session_state.snippet_folder)
                snippet_path = path_join(snippet_folder, "snippets.json")
                current, sha = load_snippets(owner, repo, branch, snippet_path, token)
                new_sha = save_snippets(owner, repo, branch, snippet_path, token, normalized, sha)
                st.session_state.snippets = normalized
                st.session_state._snip_sha = new_sha
                st.toast("스니펫 순서 저장 완료")
        except Exception as e:
            st.error(f"스니펫 순서 저장 실패: {e}")
        finally:
            try:
                if "snip_reorder" in st.query_params: del st.query_params["snip_reorder"]
                if "ts" in st.query_params: del st.query_params["ts"]
            except Exception:
                pass
            st.rerun()
except Exception:
    pass

# =============================
# TABS
# =============================
tab1, tab2, tab3, tab4 = st.tabs(["① 웹하드", "② 메모장", "③ 스니펫", "④ 설정"])

# ---------- TAB 1: 웹하드 ----------
with tab1:
    st.header("① 웹하드")

    # 40:60 + top align
    col_upload, col_list = st.columns([0.40, 0.60], vertical_alignment="top")

    # ---- 좌: 업로더 ----
    with col_upload:
        # ⬆️ Upload 아이콘으로 변경 (심플)
        st.markdown('<div class="section-label"><span class="ico">⬆️</span><span>파일 업로드</span></div>', unsafe_allow_html=True)
        files = st.file_uploader(
            "",
            accept_multiple_files=True,
            label_visibility="collapsed",
            key=f"uploader_{st.session_state.uploader_key}"
        )

        if ready and files:
            commit_msg = "Upload via My Blackhole"
            fps = tuple(sorted((f.name, getattr(f, "size", 0)) for f in files))
            if st.session_state.get("_uploaded_selection_sig") != fps:
                results = []
                with st.spinner("업로드 중…"):
                    for f in files:
                        try:
                            base_name = f.name
                            base, ext = os.path.splitext(base_name)
                            candidate = path_join(folder, base_name)
                            idx = 1
                            while get_file_sha_if_exists(owner, repo, branch, candidate, token)[0]:
                                candidate = path_join(folder, f"{base} ({idx}){ext}")
                                idx += 1
                            content = f.read()
                            if len(content) > 95 * 1024 * 1024:
                                raise RuntimeError("파일이 너무 큽니다 (API 한계 ~100MB)")
                            put_file(owner, repo, branch, candidate, content, token, commit_msg, None)
                            results.append({"name": os.path.basename(candidate), "size (KB)": round(len(content)/1024, 1), "status": "uploaded"})
                        except Exception as e:
                            results.append({"name": getattr(f, 'name', 'unknown'),
                                            "size (KB)": round(getattr(f, 'size', 0)/1024, 1) if hasattr(f, 'size') else None,
                                            "status": f"error: {e}"})
                st.session_state["_uploaded_selection_sig"] = fps
                if any(r.get("status") == "uploaded" for r in results):
                    st.session_state.uploader_key += 1
                    st.toast("업로드 완료")
                    st.rerun()
                if any(isinstance(r.get("status"), str) and r["status"].startswith("error") for r in results):
                    st.warning("일부 항목 업로드 실패")

    # ---- 우: 파일 목록 ----
    with col_list:
        st.markdown('<div class="section-label"><span class="ico">📁</span><span>파일 목록</span></div>', unsafe_allow_html=True)
        with st.container(border=True):
            try:
                items = list_folder(owner, repo, branch, ensure_folder_path(folder), token) if ready else []
                files_data = []
                for it in items:
                    if it.get("type") == "file":
                        rel_path = it.get("path")
                        raw_url = f"{gh_raw_base(owner, repo, branch)}/{rel_path}"
                        files_data.append({
                            "name": it.get("name"),
                            "size_kb": round(it.get("size", 0)/1024, 1),
                            "raw_url": raw_url,
                            "rel_path": rel_path,
                            "sha": it.get("sha"),
                        })

                if files_data:
                    is_private = repo_is_private(owner, repo, token) if ready else True
                    inline_limit_mb = float(st.session_state.get("inline_dl_limit_mb", 10))
                    inline_limit_kb = inline_limit_mb * 1024

                    def _svg_download():
                        return """<svg viewBox="0 0 24 24" width="16" height="16" fill="none"
                        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>"""

                    def _svg_link():
                        return """<svg viewBox="0 0 24 24" width="16" height="16" fill="none"
                            stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M10 13a5 5 0 0 1 0-7l2-2a5 5 0 0 1 7 7l-1 1"/>
                            <path d="M14 11a5 5 0 0 1 0 7l-2 2a5 5 0 0 1-7-7l1-1"/></svg>"""

                    def _svg_trash():
                        return """<svg viewBox="0 0 24 24" width="16" height="16" fill="none"
                        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>
                        <path d="M10 11v6"/><path d="M14 11v6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>"""

                    rows = []
                    for f in files_data:
                        data_uri = ""
                        raw_link = f["raw_url"]
                        gh_web  = f"https://github.com/{owner}/{repo}/blob/{branch}/{f['rel_path']}?raw=1"
                        try:
                            if f["size_kb"] <= inline_limit_kb:
                                _bytes = get_raw_file_bytes(owner, repo, branch, f["rel_path"], token, sha=f.get("sha"))
                                data_uri = build_data_uri(_bytes)
                        except Exception:
                            data_uri = ""
                        dl_href  = data_uri if data_uri else (gh_web if is_private else raw_link)
                        dl_title = "다운로드" + ("" if data_uri else (" (GitHub 로그인 필요)" if is_private else ""))
                        copy_url  = gh_web if is_private else raw_link
                        copy_note = "(private: 로그인 필요)" if is_private else ""

                        enc = base64.urlsafe_b64encode(f["rel_path"].encode("utf-8")).decode("ascii")
                        del_qs = f"?del={enc}&ts={int(datetime.datetime.now().timestamp())}"

                        rows.append(f"""
      <div class="row" data-del="{_html.escape(del_qs)}" title="{_html.escape(f['name'])}">
        <div class="name">{_html.escape(f['name'])}</div>
        <div class="size">{f['size_kb']} KB</div>
        <div class="btns">
          <a class="btn" href="{_html.escape(dl_href)}" download="{_html.escape(f['name'])}" title="{_html.escape(dl_title)}">
            {_svg_download()}
          </a>
          <button class="btn copy" data-copy="{_html.escape(copy_url)}" title="URL 복사 {copy_note}">
            {_svg_link()}
          </button>
          <button class="btn delete" data-del="{_html.escape(del_qs)}" title="삭제">
            {_svg_trash()}
          </button>
        </div>
      </div>
                        """.strip())

                    list_html = "\n".join(rows)
                    # 행 높이 컴팩트해졌으니 예상 높이 보정
                    comp_height = max(72, min(800, 12 + 56 * len(rows)))

                    HTML_TEMPLATE = Template(r"""
<!DOCTYPE html>
<html><head><meta charset="utf-8" />
<style>
  :root { --gap: 6px; --btn: 32px; }
  html, body { margin:0; padding:0; width:100%; }
  .wrap { width:100%; box-sizing:border-box; }
  .row {
    width: 100%;
    box-sizing: border-box;
    margin: 6px 0;
    padding: 6px 10px;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    background: #fff;
    display: grid;
    grid-template-columns: minmax(0,1fr) auto auto;
    column-gap: var(--gap);
    align-items: center;
  }
  .name {
    min-width: 0;
    font-size: .98rem;
    color:#111827;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .size { color:#6b7280; text-align:right; padding-right:2px; font-size:.9rem; }
  @media (max-width: 420px) { .size { display:none; } }
  .btns { display:flex; gap:6px; align-items:center; }
  .btn {
    width: var(--btn); height: var(--btn);
    display:flex; align-items:center; justify-content:center;
    border-radius: 10px; border: 1px solid #e5e7eb;
    background:#f9fafb; cursor:pointer; text-decoration:none;
    box-sizing: border-box;
  }
  .btn:hover { background:#eef2ff; border-color:#c7d2fe; }
  .btn:active { transform: translateY(1px); }
  .btn svg { stroke:#2563eb; width:16px; height:16px; }
</style>
</head>
<body>
  <div class="wrap" id="rows">
$LIST_HTML
    <iframe id="bg" style="display:none"></iframe>
  </div>

<script>
(function() {
  const rows = document.getElementById('rows');
  const bg = document.getElementById('bg');

  function setHeights(px) {
    try {
      const ifr = window.frameElement;
      if (!ifr) return;
      ifr.style.height = px + 'px';
      ifr.setAttribute('height', String(px));
      const p1 = ifr.parentElement;
      if (p1) {
        p1.style.height = px + 'px';
        p1.style.minHeight = px + 'px';
        p1.style.maxHeight = px + 'px';
      }
      let e = p1;
      for (let i=0; i<6 && e; i++) {
        if (e.classList && (e.classList.contains('stElementContainer') || e.classList.contains('element-container'))) {
          e.style.height = px + 'px';
          e.style.minHeight = px + 'px';
          e.style.maxHeight = px + 'px';
          break;
        }
        e = e.parentElement;
      }
    } catch(_) {}
  }
  function adjustHeight() {
    const h = Math.ceil(rows.getBoundingClientRect().height) + 8;
    setHeights(h);
  }
  window.addEventListener('load', adjustHeight);
  const ro = new ResizeObserver(() => adjustHeight());
  ro.observe(rows);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(adjustHeight).catch(()=>{});
  }

  rows.addEventListener('click', async (e) => {
    const btn = e.target.closest('.btn.copy');
    if (!btn) return;
    const url = btn.getAttribute('data-copy') || '';
    try {
      await navigator.clipboard.writeText(url);
      btn.style.background = '#dcfce7'; btn.style.borderColor = '#86efac';
    } catch(e) {
      btn.style.background = '#fee2e2'; btn.style.borderColor = '#fecaca';
    } finally {
      setTimeout(() => { btn.style.background=''; btn.style.borderColor=''; }, 800);
    }
  });

  rows.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn.delete');
    if (!btn) return;
    const del = btn.getAttribute('data-del');
    if (!del) return;

    let target = '';
    try {
      const topLoc = window.top.location;
      const u = new URL(topLoc.origin + topLoc.pathname);
      const delUrl = new URL(del, topLoc.origin);
      u.searchParams.set('del', delUrl.searchParams.get('del'));
      u.searchParams.set('ts', Date.now().toString());
      target = u.toString();
    } catch(_) {
      const here = new URL(window.location.href);
      here.search = ''; here.pathname = '/';
      here.searchParams.set('del', (new URL(del, window.location.origin)).searchParams.get('del'));
      here.searchParams.set('ts', Date.now().toString());
      target = here.toString();
    }

    const row = btn.closest('.row');
    if (row) {
      row.style.opacity = '0.6';
      setTimeout(() => {
        row.remove();
        adjustHeight();
        setTimeout(adjustHeight, 50);
        setTimeout(adjustHeight, 150);
      }, 120);
    }

    const bg = document.getElementById('bg');
    bg.src = target;
  });
})();
</script>
</body></html>
""")
                    html_render = HTML_TEMPLATE.substitute(LIST_HTML=list_html)
                    st_html(html_render, height=comp_height)
                else:
                    st.info("이 폴더에 파일이 없거나 접근할 수 없습니다.")
            except Exception as e:
                st.error(f"목록 조회 오류: {e}")

# ---------- TAB 2: 메모 ----------
with tab2:
    st.header("② 크로스디바이스 메모장")

    memo_folder = ensure_folder_path(st.session_state.memo_folder)
    memo_filename = path_join(memo_folder, "memo.md")
    st.caption(f"메모 저장 위치: `{memo_filename}`")

    if ready and not st.session_state._memo_autoloaded:
        try:
            sha, info = get_file_sha_if_exists(owner, repo, branch, memo_filename, token)
            if info and info.get("content"):
                decoded = base64.b64decode(info["content"]).decode("utf-8", errors="replace")
                st.session_state.memo_area = decoded
        except Exception:
            pass
        finally:
            st.session_state._memo_autoloaded = True

    if "load_memo" not in st.session_state:  st.session_state.load_memo  = False
    if "clear_memo" not in st.session_state: st.session_state.clear_memo = False

    if st.session_state.load_memo and ready:
        try:
            sha, info = get_file_sha_if_exists(owner, repo, branch, memo_filename, token)
            if info and info.get("content"):
                decoded = base64.b64decode(info["content"]).decode("utf-8", errors="replace")
                st.session_state.memo_area = decoded
                st.toast("메모 불러옴")
            else:
                st.session_state.memo_area = ""
                st.toast("메모 파일이 아직 없습니다.")
        except Exception as e:
            st.error(f"불러오기 실패: {e}")
        finally:
            st.session_state.load_memo = False

    if st.session_state.clear_memo and ready:
        try:
            sha, _ = get_file_sha_if_exists(owner, repo, branch, memo_filename, token)
            put_file(owner, repo, branch, memo_filename, b"", token, "Clear memo", sha)
            st.session_state.memo_area = ""
            st.toast("메모를 비웠습니다")
        except Exception as e:
            st.error(f"Clear 실패: {e}")
        finally:
            st.session_state.clear_memo = False

    st.text_area("여기에 붙여넣고 저장하세요 (Markdown)", key="memo_area", height=220)

    st.markdown('<div class="memo-btn-row">', unsafe_allow_html=True)
    c1, c2, c3, _sp = st.columns([0.12, 0.22, 0.12, 1], gap="small")
    with c1:
        if st.button("저장", key="memo_save_btn", type="primary", use_container_width=True, help="메모를 현재 파일에 저장"):
            try:
                sha, _ = get_file_sha_if_exists(owner, repo, branch, memo_filename, token)
                body = st.session_state.get("memo_area", "")
                put_file(owner, repo, branch, memo_filename, body.encode("utf-8"), token, "Update memo", sha)
                st.toast("메모 저장 완료")
            except Exception as e:
                st.error(f"저장 실패: {e}")
    with c2:
        if st.button("불러오기", key="memo_load_btn", use_container_width=True):
            st.session_state.load_memo = True
            st.rerun()
    with c3:
        if st.button("Clear", key="memo_clear_btn", use_container_width=True):
            st.session_state.clear_memo = True
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ---------- TAB 3: 스니펫 ----------
with tab3:
    st.header("③ Text Snippet")

    snippet_folder = ensure_folder_path(st.session_state.snippet_folder)
    snippet_path = path_join(snippet_folder, "snippets.json")

    if ready and not st.session_state._snippets_loaded:
        try:
            snips, sha = load_snippets(owner, repo, branch, snippet_path, token)
            st.session_state.snippets = snips
            st.session_state._snip_sha = sha
        except Exception as e:
            st.warning(f"스니펫 로드 실패: {e}")
        finally:
            st.session_state._snippets_loaded = True

    if st.session_state._snip_clear:
        try:
            st.session_state["snip_input"] = ""
        except Exception:
            pass
        st.session_state._snip_clear = False

    labels_html = []
    for idx, snip in enumerate(st.session_state.snippets):
        obj = _normalize_snippet_item(snip)
        t = obj.get("t", "")
        hint = obj.get("hint", "")
        label = (t or "").replace("\n", " ").strip()
        safe_label = _html.escape(label)
        safe_t = _html.escape(t or "")
        safe_hint = _html.escape(hint or "")
        title_attr = f' title="{safe_hint}"' if hint else ""
        labels_html.append(
            f'<button class="chip" draggable="true" data-idx="{idx}" data-t="{safe_t}" data-hint="{safe_hint}"{title_attr}>'
            f'  <span class="txt">{safe_label}</span>'
            f'</button>'
        )
    chips_html = "\n".join(labels_html) if labels_html else '<span class="empty">등록된 스니펫이 없습니다</span>'

    SNIP_TEMPLATE = Template(r"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
  .bar {
    width:100%; box-sizing:border-box; padding:8px 4px;
    white-space: nowrap; overflow-x: auto; overflow-y: hidden;
    border: 1px dashed #e5e7eb; border-radius: 12px; background:#fff;
  }
  .chip {
    display:inline-flex; align-items:center;
    height:36px; max-width: 280px;
    padding: 0 12px; margin-right:8px;
    border-radius: 10px; border:1px solid #e5e7eb;
    background:#f9fafb; cursor:grab; user-select:none;
  }
  .chip:hover { background:#eef2ff; border-color:#c7d2fe; }
  .chip:active { cursor:grabbing; }
  .chip.dragging { opacity:.6; border-style:dashed; }
  .chip .txt { white-space: nowrap; overflow:hidden; text-overflow: ellipsis; }
  .empty { color:#9ca3af; margin-left:4px; }
</style>
</head>
<body>
  <div class="bar" id="snip-bar">
    $CHIPS_HTML
    <iframe id="snip-bg" style="display:none"></iframe>
  </div>
<script>
(function(){
  const bar = document.getElementById("snip-bar");
  const bg  = document.getElementById("snip-bg");
  let dragging = null;

  function chipEls() { return Array.from(bar.querySelectorAll('.chip')); }

  function getAfterElement(container, x) {
    const els = chipEls().filter(el => el !== dragging);
    let closest = {offset: Number.NEGATIVE_INFINITY, element: null};
    els.forEach(el => {
      const box = el.getBoundingClientRect();
      const offset = x - (box.left + box.width/2);
      if (offset < 0 && offset > closest.offset) {
        closest = {offset, element: el};
      }
    });
    return closest.element;
  }

  bar.addEventListener('dragstart', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    dragging = chip;
    chip.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', chip.getAttribute('data-t') || ''); } catch(_) {}
  });

  bar.addEventListener('dragover', (e) => {
    if (!dragging) return;
    e.preventDefault();
    const after = getAfterElement(bar, e.clientX);
    if (after == null) bar.appendChild(dragging);
    else bar.insertBefore(dragging, after);
  });

  bar.addEventListener('dragend', () => {
    if (!dragging) return;
    dragging.classList.remove('dragging');
    dragging = null;

    const chips = chipEls();
    const arr = chips.map(el => {
      const t = el.getAttribute('data-t') || '';
      const hint = el.getAttribute('data-hint') || '';
      return hint ? {t, hint} : {t};
    });
    try {
      const json = JSON.stringify(arr);
      const b64 = btoa(unescape(encodeURIComponent(json)));
      bg.src = "?snip_reorder=" + encodeURIComponent(b64) + "&ts=" + Date.now();
    } catch(e) {}
  });

  bar.addEventListener("click", async (e) => {
    const btn = e.target.closest(".chip");
    if(!btn) return;
    const raw = btn.getAttribute("data-t") || "";
    try {
      await navigator.clipboard.writeText(raw);
      btn.style.background = "#dcfce7"; btn.style.borderColor = "#86efac";
    } catch(err) {
      btn.style.background = "#fee2e2"; btn.style.borderColor = "#fecaca";
    } finally {
      setTimeout(()=>{ btn.style.background=''; btn.style.borderColor=''; }, 800);
    }
  });

  bar.addEventListener("contextmenu", (e) => {
    const btn = e.target.closest(".chip");
    if(!btn) return;
    e.preventDefault();
    const idx = btn.getAttribute("data-idx");
    try {
      btn.style.opacity = "0.5";
      bg.src = "?snip_del=" + encodeURIComponent(idx) + "&ts=" + Date.now();
      setTimeout(()=>{ btn.remove(); }, 120);
    } catch(err) {}
  });
})();
</script>
</body></html>
""")

    st_html(SNIP_TEMPLATE.substitute(CHIPS_HTML=chips_html), height=100)

    st.caption("위: 스니펫 — 드래그로 순서 변경 · 클릭=복사 · 우클릭=삭제 · '제목:내용' 입력 시 제목은 툴팁, 내용은 버튼 라벨/복사값")

    st.text_area("등록할 텍스트 (예: 카드번호:112344)", key="snip_input", height=100, placeholder="여기에 텍스트를 입력하세요. '제목:내용' 형식을 쓰면 제목은 툴팁, 내용은 버튼 라벨이 됩니다.")
    col_reg, _sp = st.columns([0.15, 1], gap="small")
    with col_reg:
        if st.button("등록", disabled=not ready):
            raw_in = (st.session_state.get("snip_input") or "").rstrip()
            if not raw_in:
                st.warning("빈 텍스트는 등록하지 않습니다.")
            else:
                try:
                    hint = None
                    text = raw_in
                    if ":" in raw_in:
                        left, right = raw_in.split(":", 1)
                        hint = left.strip() or None
                        text = right.strip()
                    item = {"t": text}
                    if hint:
                        item["hint"] = hint

                    current, sha = load_snippets(owner, repo, branch, snippet_path, token)
                    current.append(item)
                    new_sha = save_snippets(owner, repo, branch, snippet_path, token, current, sha)
                    st.session_state.snippets = current
                    st.session_state._snip_sha = new_sha
                    st.session_state._snip_clear = True
                    st.toast("스니펫 등록 완료")
                    st.rerun()
                except Exception as e:
                    st.error(f"등록 실패: {e}")

# ---------- TAB 4: 설정 ----------
with tab4:
    _settings_panel()
    st.markdown("---")
    st.caption("Tip: 설정 탭에서 업로드/메모/스니펫 폴더와 인라인 다운로드 한도를 변경할 수 있어요. GH_TOKEN은 secrets.toml에 보관됩니다.")
