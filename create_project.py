import os
from pathlib import Path

def create_project_structure():
    # ベースとなるディレクトリパス
    base_path = Path(r"C:\Users\zabie\FreelanceChats\503_着席検知システム")

    # 今後を見据えたディレクトリ構成
    directories =[
        "src",                  # Pythonソースコード一式
        "src/core",             # センサー監視などのコアロジック
        "src/web",              # FlaskのWebサーバー関連
        "src/web/templates",    # HTMLテンプレート
        "src/web/static",       # CSSやJSファイル
        "src/db",               # データベース操作関連
        "src/utils",            # 通知や温度チェックなどの便利ツール
        "termux",               # Android(Termux)専用のスクリプト群
        "termux/boot",          # Termux:Boot用スクリプト
        "data",                 # SQLiteのDBファイル保存先（gitignore対象）
        "logs",                 # 動作ログ保存先
        "tests",                # テストコード用
    ]

    # 今後を見据えたファイル構成（全て空ファイルで作成）
    files =[
        # ドキュメント・設定ファイル
        "README.md",            # プロジェクトの概要やセットアップ手順
        "requirements.txt",     # pip install用のパッケージリスト
        ".env",                 # Webhook URLなどの機密情報・環境変数
        ".env.example",         # .envのサンプル（gitに上げる用）
        ".gitignore",           # Git管理から外すファイル指定

        # エントリーポイント
        "main.py",              # システム全体の起動スクリプト

        # コアロジック (src/core)
        "src/core/__init__.py",
        "src/core/sensor.py",   # termux-sensorの非同期読み取りロジック

        # Webサーバー (src/web)
        "src/web/__init__.py",
        "src/web/app.py",       # Flaskアプリ本体
        "src/web/templates/index.html", # 状態表示用Webページ
        "src/web/static/style.css",     # Webページのデザイン

        # データベース操作 (src/db)
        "src/db/__init__.py",
        "src/db/models.py",     # DBのテーブル定義やCRUD処理

        # ユーティリティ (src/utils)
        "src/utils/__init__.py",
        "src/utils/notifier.py", # Discord/Telegram通知ロジック
        "src/utils/hardware.py", # 温度チェックやバッテリー監視

        # Termux用スクリプト (termux)
        "termux/setup.sh",           # Termux環境の初回構築自動化スクリプト
        "termux/boot/start_logger.sh",# 自動起動スクリプト

        # データ・ログディレクトリの維持用（空ディレクトリがGitで無視されないようにするため）
        "data/.gitkeep",
        "logs/.gitkeep",
        
        # テスト用
        "tests/__init__.py",
        "tests/test_sensor.py",
    ]

    print(f"🚀 プロジェクト生成を開始します...\nターゲット: {base_path}\n")

    # ディレクトリの作成
    for dir_name in directories:
        dir_path = base_path / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"📁 作成済 (Dir) : {dir_name}")

    # 空ファイルの作成
    for file_name in files:
        file_path = base_path / file_name
        # ファイルが存在しない場合のみ作成（上書き防止）
        if not file_path.exists():
            file_path.touch()
            print(f"📄 作成済 (File): {file_name}")
        else:
            print(f"⏭️ スキップ (既存): {file_name}")

    print("\n✅ 全てのディレクトリと空ファイルの生成が完了しました！")

if __name__ == "__main__":
    create_project_structure()