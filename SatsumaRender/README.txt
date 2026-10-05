薩摩会議 Render用 音声中継とライブ字幕 v1.0

このフォルダはRender Web Serviceのソースコードです。
Renderの「New Web Service」はZIPを直接アップロードする画面ではありません。
まずGitHub等のリポジトリへ、このフォルダの中身を登録します。

A GitHubにコードを登録
1. GitHubにログインし、New repositoryを選ぶ。
2. Repository nameを satsuma-live にする。Privateを推奨。
3. Add a READMEをチェックしてリポジトリを作る。
4. Add file → Upload filesで、このZIPを展開したフォルダの「中身」をアップロード。
   フォルダの親ごとではなく、app.py / requirements.txt / render.yaml / public / tests が
   リポジトリの一番上にある形にしてください。
5. Commit changesを押す。APIキー・トークンはGitHubに登録しない。

B Renderでリポジトリを選ぶ
1. New → Web Service → Git Providerを選択。
2. GitHubを接続し、satsuma-liveリポジトリへのアクセスを許可。
3. リポジトリが一覧に出たらConnectを押す。
4. 設定:
   Name: satsuma-live（使用済みなら任意の名前）
   Region: Singapore
   Language / Runtime: Python 3
   Branch: main（作成したブランチ）
   Root Directory: 空欄
   Build Command: pip install -r requirements.txt
   Start Command: python app.py
   Compute Plan: 0.5c-512mb（旧Starter、有料）
   Instance count: 1
   Health Check Path: /health
5. Environment Variablesに以下を追加:
   OPENAI_API_KEY: API Platformで作成したキー
   SENDER_TOKEN: 自分で作成した長いランダム文字列（32文字以上推奨）
   PYTHON_VERSION: 3.12.12
   トークン生成例: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   OPENAI_API_KEYはChatGPTのパスワードとは別です。キーをチャットに貼る必要はありません。
6. Create Web Serviceでデプロイ。
7. Renderの自動デプロイ設定をOffにし、本番中はデプロイや設定変更を行わない。

C 公開後のURL
Renderで発行された https://サービス名.onrender.com に対して:
 /                 言語選択
 /sender/index.html 会場Macの2系統音声送信画面
 /live/ja           日本語字幕
 /live/zh           繁体字字幕
 /ingest            音声送信用WebSocket（wss://）
 /health            ヘルスチェック
会場MacはChromeで /sender/index.html を開く。この場合Pythonのローカル起動は不要。
送信先URLは自動設定される。SENDER_TOKENの値を送信トークン欄に入力する。
OPENAI_API_KEYを会場画面に入れてはいけない。
前のローカル送信アプリも使えるが、Render同梱の新版は接続初期化待ちと混雑再接続を改善。
参加者は /live/ja を、台湾ゲストは /live/zh をブラウザで開く。

D 本番前の確認
・2入力が独立したステレオとしてChromeに公開されるUSBインターフェースを使用。
・片マイクずつレベルメーターを確認する。2ch表示のみでは分離を保証しない。
・OPENAI_API_KEYのプロジェクトで当該モデルと料金・利用上限を確認。
・実音声の中国語→日本語、日本語→中国語の翻訳品質・固有名詞・数字を確認。
・90分の連続運転、50台相当の閲覧、再接続を本番回線・実機で試す。
・台湾向けの出力はAPIにzhを指定し、字幕全文をOpenCC s2twで繁体字へ変換する。
  表記の変換であり、台湾で自然な言い回し・固有名詞の正しさは人が確認する。

E 構成と制限
・常時稼働する単一Pythonプロセスで2翻訳セッションを保持。
・音声は各系統100ms、24kHz PCM16LE monoを受信し、APIへ継続送信。
・APIが生成する音声は破棄し、字幕テキストだけをSSEで最大100接続へ配信。
・入力1 中国語→日本語(ja)、入力2 日本語→中国語(zh)。無音も継続送信。
・字幕は各言語最新6,000文字をメモリ内に保持。録音・字幕のディスク保存なし。
・新規「送信開始」で字幕をクリア。同じrun_idの再接続では字幕を保持。
・サーバー再起動で字幕履歴は消える。インスタンスは1台、複数workerは使わない。
・字幕ページは公開URLを知る人が閲覧可能。秘密の会議には閲覧者認証を別途追加。
・回線断の音声は復元しない。APIセッション切断後は会場アプリが再接続。
・同時送信者は1名。前接続の終了処理中は再接続待ち。
・OpenAIの利用権限エラー等はRender Logsで確認。ログにAPIキー・音声本文は残さない。
・セッション末尾の翻訳はsession.close後に最大8秒待つ。
・Renderの再起動・メンテナンスやOpenAIのセッション制限に対する完全な無停止保証はない。

F 検証状況
自動テストは、実際のHTTP/WebSocketサーバーと模擬OpenAIサーバーで実施。
2方向のルーティング、50閲覧接続、字幕配信、繁体字変換、認証拒否、不正音声の拒否、
停止時の末尾字幕の受信を確認済み。
本物のOpenAI API、Render上のデプロイ、Mac実機での動作は未確認。

ローカルで試す場合:
 python3 -m venv .venv
 .venv/bin/pip install -r requirements.txt
 SENDER_TOKEN='自分の長いトークン' MOCK_TRANSLATION=1 .venv/bin/python app.py
Chromeで http://localhost:10000/sender/index.html を開き、
ws://localhost:10000/ingest を指定。MOCK_TRANSLATION=1は翻訳しない接続試験モード。
Renderの本番環境にはMOCK_TRANSLATIONを設定しない。

自動テスト:
 python3 -m unittest discover -s tests -v

参照:
https://render.com/docs/web-services
https://render.com/docs/websocket
https://developers.openai.com/api/docs/guides/realtime-translation
