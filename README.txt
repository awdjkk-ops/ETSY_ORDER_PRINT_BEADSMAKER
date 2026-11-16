Etsy 주문서 자동 출력 프로그램 - 사용 안내
==========================================

결제 완료 + 미발송 주문을 Etsy에서 가져와
  1) 첫 장: 상품별 합계(피킹 리스트)
  2) 주문 1건당 1장: 배송지, 상품/옵션, 개인화 문구(노란 강조), 구매자 메모, 선물 메시지
를 PDF로 만들어 프린터로 바로 보냅니다.
한 번 출력한 주문은 기억해 두었다가 다음 실행 때 다시 출력하지 않습니다.


[처음 한 번만 하는 설정]

0. Python 설치
   - https://www.python.org/downloads/ 에서 최신 버전 설치
   - 설치 첫 화면에서 "Add python.exe to PATH" 체크를 꼭 켜세요.

1. 1_install.bat 더블클릭  → 필요한 부품 설치

2. config.ini 를 메모장으로 열어서
   keystring = (Etsy 대시보드의 Keystring)
   shared_secret = (Shared Secret, 눈 아이콘 눌러서 보이는 값)
   입력 후 저장

3. 2_login.bat 더블클릭
   → 브라우저에 Etsy 화면이 뜨면 "Grant access(허용)" 클릭
   → "연결 완료" 문구가 나오면 끝

4. 3_test_sample.bat 더블클릭 (선택)
   → 가짜 주문 3건으로 주문서 모양을 미리 확인


[매일 사용]

- print_orders.bat 더블클릭 → 새 주문만 출력
- 매일 자동 실행을 원하면 4_schedule_daily_9am.bat 을 한 번 실행
  (매일 오전 9시. 시간 변경은 Windows "작업 스케줄러"에서 "Etsy Order Print" 수정)
  ※ 그 시간에 PC가 켜져 있어야 합니다.


[자동 인쇄가 잘 안 될 때]

Windows에서 PDF를 창 없이 바로 인쇄하려면 무료 PDF 뷰어 "SumatraPDF" 설치를 권장합니다.
(https://www.sumatrapdfreader.org) 설치하면 프로그램이 자동으로 찾아서 사용합니다.
SumatraPDF가 없으면 PC의 기본 PDF 프로그램으로 인쇄를 시도하는데,
Edge 브라우저가 기본 PDF 프로그램이면 인쇄가 안 될 수 있습니다.

인쇄 없이 PDF만 보고 싶으면 config.ini 에서 auto_print = no


[다시 출력하고 싶을 때]

명령 프롬프트에서 이 폴더로 이동 후:
  python orderprint.py --all        이미 출력한 것까지 미발송 주문 전부 다시 출력
  python orderprint.py --no-print   PDF만 만들기
만들어진 PDF는 모두 output 폴더에 남아 있습니다.


[파일 설명 / 보안]

config.ini           설정 (API 키 포함 - 공유 금지)
token.json           Etsy 연결 정보 (공유 금지, 지우면 2_login.bat 다시 실행)
printed_orders.json  이미 출력한 주문번호 목록
orderprint.log       실행 기록 (문제 생기면 이 파일 내용을 확인)

이 프로그램은 주문을 "읽기"만 하며, Etsy에서 아무것도 수정하지 않습니다.
90일 이상 실행하지 않으면 연결이 만료될 수 있어요 → 2_login.bat 다시 실행.
