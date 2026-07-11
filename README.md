# 혜경 전자책 스튜디오

PDF, TXT, Markdown 원고는 EPUB 3·PDF·Markdown으로, 완성된 HTML 전자책 ZIP은 원본 화면을 보존한 HTML·ZIP·추출 텍스트로 출판하는 한국어 Flask 웹앱입니다.

## 주요 기능

- PC 4열·모바일 2열 반응형 전자책 서재와 한글 표지 썸네일
- 책 소개를 우선 사용하고, 없으면 원고 앞부분 최대 2,000자만 OpenAI Image API로 보내 글자 없는 표지 배경 자동 생성
- 실제 한글 폰트로 제목·저자를 합성하고 같은 `cover.png`를 EPUB·웹 리더·서재 썸네일에 공용
- PDF 원본을 그대로 결과 PDF로 보존하는 즉시 출판
- 원본 PDF 각 페이지를 실제 썸네일과 고해상도 화면으로 보여주는 웹 리더
- TXT/Markdown의 한글 PDF 및 EPUB 3 생성
- `index.html`과 로컬 CSS·JavaScript·이미지·글꼴을 담은 ZIP을 원래 화면 그대로 HTML 전자책으로 출판
- HTML 책을 별도 `html.epub.xsw.kr` 출처와 토큰·CSP sandbox로 격리해 검색/목차 스크립트는 유지하고 관리자 세션은 보호
- 스캔 PDF도 원본 PDF는 출판하고 웹/EPUB에는 텍스트 추출 불가 안내 제공
- 모바일 PDF 리더의 숨김형 페이지 탭과 좌우 스와이프, PC의 고정 페이지 목록
- 비로그인 공개 서재와 무료 전체 열람·유료 앞부분 샘플
- 책별 공개 상태·가격·샘플 쪽수·상담 할인 혜택 설정과 안전한 보관 처리
- 운명서재 상품·로그인·결제·구매자 전체 열람 연계
- 설정에서 상단 바·홈 메인·하단 바·강조색을 나눠 편집하고 실시간 미리보기
- 기존 YouTube Shorts 사용자 계정과 NAVER SMTP 비밀번호 찾기 연동

## 개발 기록

- [2026-07-11 · EPUBIA 1.4.1 출판·공개 서재·운명서재 연계](docs/2026-07-11-development-log.md)

## 실행

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
PORT=5010 ./venv/bin/python app.py
```

운영 실행은 `./start.sh`를 사용합니다. 현재 설정은 Gunicorn을 `127.0.0.1:5010`, 기본 2 workers로 실행하며 Caddy의 `epub.xsw.kr` 및 HTML 격리용 `html.epub.xsw.kr` 프록시를 전제로 합니다.

PM2로 관리할 경우:

```bash
pm2 start ./start.sh --name epubia --interpreter bash
pm2 restart epubia
./stop.sh
```

## 환경 설정

`.env.example`을 복사한 뒤 최소한 다음 값을 실제 환경에 맞게 설정합니다.

- `EPUBIA_SECRET_KEY`: 모든 Gunicorn worker가 공유할 긴 무작위 세션 키
- `YOUTUBE_SHORTS_ROOT`: 공유 사용자 파일이 있는 프로젝트 경로
- `EPUBIA_ALLOWED_USERS`: 전자책 스튜디오 접근 허용 계정
- `EPUBIA_SITE_EDITORS`: 상단·홈·하단 프론트 화면을 편집할 계정. 쉼표로 명시한 계정만 편집 가능
- `OPENAI_API_KEY`: 책 소개 또는 원고 앞부분 기반 AI 표지 생성용 키. 프론트나 manifest에는 노출하지 않음
- `JARVIS_BOT_ROOT`, `NAVER_MAIL_*`: 비밀번호 찾기 메일 연동
- `SESSION_COOKIE_SECURE=1`: HTTPS 운영 환경의 보안 쿠키
- `EPUBIA_PUBLIC_ORIGIN`, `EPUBIA_HTML_CONTENT_ORIGIN`: 메인 서비스와 HTML 격리 리더의 HTTPS 출처
- `EPUBIA_HTML_CONTENT_TOKEN_TTL_SECONDS`: HTML 읽기 capability 주소의 유효시간(기본 12시간, 최대 24시간)
- `SAJU_LIBRARY_ORIGIN`: 유료책 구매를 연결할 운명서재 출처
- `EBOOK_ACCESS_SECRET` 또는 `EBOOK_ACCESS_SECRET_FILE`: 구매자 전체 열람권을 양쪽 서비스가 검증하는 공유 키

프론트 편집 권한이 있는 계정은 설정 화면에서 원고 업로드 한도(10~500MB), AI 표지 사용 여부·모델·품질과 상단·홈·하단 화면 문구를 변경할 수 있습니다. 기본 업로드 한도는 100MB입니다. 저장한 런타임 설정은 `workspace/site-settings.json`에 보관되며 Git에는 포함되지 않습니다.

## 출판 데이터

- 업로드: `workspace/uploads/`
- 책별 결과: `workspace/books/<timestamp-uuid>/`
- PDF/TXT/Markdown 결과: `cover.png`, `source.txt`, `.md`, `.epub`, `.pdf`, `manifest.json`
- 완성형 HTML ZIP 결과: `cover.png`, `source.txt`, `.md`, 원본 `.zip`, 압축을 푼 `html/`, `manifest.json` (이미 완성된 화면을 다시 EPUB/PDF로 조판하지 않음)
- 표지 배경: `static/images/covers/cover-bg-*.png`

PDF 입력의 결과 PDF는 원본 바이트를 그대로 복사하므로 기존 한글 글꼴과 레이아웃이 손상되지 않습니다. 웹 리더는 PyMuPDF로 필요한 페이지만 PNG로 렌더링해 캐시하며, PC에서는 왼쪽 페이지 썸네일 목록을 고정하고 모바일에서는 옆 탭으로 열고 닫습니다. TXT/Markdown은 시스템 Noto CJK 또는 번들 한글 글꼴을 사용합니다.

HTML 전자책 ZIP은 최상위(또는 하나의 포장 폴더)에 `index.html`이 정확히 하나 있어야 합니다. 자산은 상대 경로로 연결하고 외부 CDN 대신 ZIP 안에 포함해야 합니다. 서버는 경로 탈출, 심볼릭 링크, 실행 파일, 중첩 압축, 과도한 파일 수·압축률을 거부하며, 게시된 HTML은 메인 로그인 출처와 분리된 iframe에서 실행합니다. 읽기 주소는 책 버전과 만료 시각을 함께 서명하므로 공유되더라도 기본 12시간 뒤 만료되며, 운영 access log에는 전체 경로를 남기지 않습니다.

## 테스트

```bash
./venv/bin/python -m pytest -q
node --check static/js/flipbook.js
node --check static/js/library.js
node --check static/js/settings.js
node --check static/js/pdf-reader.js
```

2026-07-11 운영 배포 기준 자동 테스트 **161개**와 PC·모바일 공개 서재/샘플 리더 브라우저 검증을 통과했습니다.

## 레거시 코드

`epubia.py`, `gui.py`, `template/`, `setup.py`, `README.txt`는 Python 2/wxPython 기반 구형 데스크톱판입니다. 현재 Flask 운영 경로는 `start.sh → app.py → ebook_pipeline.py`이며 레거시 모듈을 호출하지 않습니다.
