# chair-logger

古いAndroidスマホ（Termux）の重力センサーで着席・離席を自動検知して、Discordに作業時間を全自動記録するIoTシステム。クラウドも月額費用も0円。

## 動作イメージ

```
[着席] 2026-05-09 10:23:45 作業を開始しました。  🔋82%  Δ: 0.234  ⏱ 1時間23分/6時間（23%）
[離席] 2026-05-09 12:47:01 席を離れました。  🔋79%  Δmax: 0.031  ⏱ 3時間10分/6時間（52%）
```

```
📊 着席サマリー 5月9日（金）

09:15 〜 11:47  （2時間32分）
13:02 〜 17:30  （4時間28分）

合計着席: 7時間0分
```

## 構成

| 要素 | 使ったもの |
|---|---|
| 端末 | 古いAndroid（Android 11以上推奨） |
| 環境 | Termux + Termux:API |
| 言語 | Python 3 |
| DB | SQLite（スマホ内ローカル） |
| 通知 | Discord Webhook |
| UI | Flask WebUI（ポート8080） |

```
[古いAndroidスマホ]
  └── Termux（Python main.py）
       ├── SensorMonitor スレッド      ← 30秒ごとにセンサー取得・判定
       ├── TempMonitor スレッド        ← 60秒ごとにバッテリー温度監視
       ├── SummaryScheduler スレッド   ← 毎日22:00にサマリー送信
       └── Flask WebUI                ← PCブラウザからログ確認（ポート8080）
```

## セットアップ

### 1. Termuxのインストール

**F-Droid（推奨）** からインストールしてください。Google Play版は非推奨（APIが制限されています）。

- [F-Droid: Termux](https://f-droid.org/packages/com.termux/)
- [F-Droid: Termux:API](https://f-droid.org/packages/com.termux.api/)

### 2. Termux内でのセットアップ

```bash
# 基本パッケージ
pkg update && pkg install python git

# Python依存ライブラリ
pip install requests flask python-dotenv

# リポジトリをクローン
git clone https://github.com/zabieru314/chair-logger.git
cd chair-logger
```

### 3. 設定ファイルの作成

`.env` ファイルをスマホ上で作成（gitignore対象・このファイルは絶対にpushしないこと）：

```bash
cat > .env << 'EOF'
WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
SUMMARY_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_SUMMARY_WEBHOOK_URL
EOF
```

閾値や通知時刻などは `.env.config`（git管理）で変更できます：

```ini
VARIANCE_THRESHOLD=0.04      # XYZ差分のしきい値（小さいほど敏感）
DEBOUNCE_LEFT_SEC=600        # 離席確定までの秒数（10分）
DAILY_GOAL_MINUTES=360       # 1日の目標着席分数（0=表示なし）
SUMMARY_TIME=22:00           # 日次サマリー送信時刻
SENSOR_POLL_INTERVAL_SEC=30  # ポーリング間隔
MAX_TEMP_CELSIUS=40.0        # 過熱と判断する温度上限
```

### 4. 起動

```bash
python main.py
```

## ⚠️ 重要な警告

**`pkill` や `SIGKILL` は絶対に実行しないでください。**

`pkill termux-sensor` を実行すると、AndroidのIPC機構（binder）が破壊され、センサーが一切応答しなくなります。

- 正しい終了方法: `Ctrl+C` でmain.pyを停止するだけ（センサーは `-n 1` で自然終了するため追加操作不要）
- binder が壊れた場合の復旧: `termux-sensor -c` → それでもダメなら設定からTermux:APIを「強制停止」

## 安全に関する注意

本システムは古いスマートフォンを充電状態・画面オン・クッション上に置いて運用します。バッテリーの劣化具合によっては過熱・膨張のリスクがあります。システム内に40℃超で自動休止する安全装置を実装していますが、**自己責任のもとで運用してください。**

## 開発の詳細

[Zennの記事]() に開発の経緯・失敗談・アルゴリズムの詳細をまとめています。

## ライセンス

MIT
