# 503_着席検知システム

## 【絶対ルール】このプロジェクトは必ず Opus モデルで作業すること

このプロジェクトはセンサー・Android・Termux の複雑なデバッグが多いため、
必ず `--model opus` で起動した Claude Code で作業すること。

Sonnet で誤って開いた場合は即座に再起動する：
```
claude --model opus
```

---

## プロジェクト概要

古いAndroidスマホ（Termux環境）で着席・離席を自動検知してDiscordに通知するIoTシステム。

---

## 現在の実装状態（2026-05-08 完了）

### 検知方式：30秒ポーリング + XYZ差分

Popen連続ストリームを廃止し、30秒ごとに `termux-sensor -n 1` で1点取得する方式に変更。

```
delta = |ΔX| + |ΔY| + |ΔZ|  （前回取得値との差分）
delta >= VARIANCE_THRESHOLD → 着席（動きあり）
delta <  VARIANCE_THRESHOLD が DEBOUNCE_LEFT_SEC 秒継続 → 離席確定
```

実測値（2026-05-08）:
- 着席中 delta ≈ 0.10〜0.30（体重移動・呼吸でXYZが揺れる）
- 離席時（クッションドリフト）delta ≈ 0.006〜0.009
- 閾値: `VARIANCE_THRESHOLD=0.03`

### 通知の2層構造

| 通知 | タイミング | 送信先 |
|------|-----------|--------|
| リアルタイム（着席/離席） | 状態確定ごと | デバッグチャンネル（WEBHOOK_URL） |
| 日次サマリー | 毎日22:00 | サマリーチャンネル（SUMMARY_WEBHOOK_URL） |

サマリー例：
```
📊 着席サマリー 5月8日（木）
09:15 〜 11:45  （2時間30分）
13:00 〜 15:30  （2時間30分）
合計着席: 5時間0分
```

---

## デプロイ方針（重要）

**PC側で編集・push → スマホ側は `git pull` + 再起動のみ。**
スマホで直接ファイルを編集しない。

### 設定ファイル構成

| ファイル | git管理 | 用途 |
|--------|--------|------|
| `.env.config` | ✅ 管理対象 | 設定値全般（VARIANCE_THRESHOLD等） |
| `.env` | ❌ gitignore | WEBHOOK_URL のみ（スマホのみに存在） |

設定変更手順：
1. PC側で `.env.config` を編集
2. `git push`
3. スマホで `git pull` → `python main.py` 再起動

### 現在の `.env.config` 設定値

```
SUMMARY_WEBHOOK_URL=https://discord.com/api/webhooks/1502313121769848892/...
SUMMARY_TIME=22:00
VARIANCE_THRESHOLD=0.03
DEBOUNCE_DELAY_SEC=5        # 着席確定（テスト用・運用は不要）
DEBOUNCE_LEFT_SEC=300       # 離席確定（5分）
SENSOR_POLL_INTERVAL_SEC=30 # ポーリング間隔
MAX_TEMP_CELSIUS=40.0
COOLDOWN_SLEEP_SEC=300
TEMP_CHECK_INTERVAL_SEC=60
FLASK_HOST=0.0.0.0
FLASK_PORT=8080
```

---

## スマホ側での操作（再起動手順）

```bash
cd ~/chair_logger/chair_logger
git pull
# Ctrl+C で旧プロセスを止めてから
python main.py
```

---

## ファイル構成

```
main.py               エントリーポイント + SummaryScheduler
calibrate.py          閾値キャリブレーション（-n 20 方式）
.env.config           設定値（git管理対象）
.env                  WEBHOOK_URL のみ（gitignore・スマホのみ）
src/core/sensor.py    30秒ポーリング + XYZ差分判定
src/db/models.py      SQLite操作 + calc_daily_summary
src/utils/notifier.py Discord通知 + notify_summary
src/utils/hardware.py 温度監視
src/web/app.py        Flask WebUI（ポート8080）
```

---

## Termux:API センサーの絶対ルール

### -n 1 方式なので SIGINT は不要

`subprocess.run -n 1` はプロセスが自然終了するため SIGINT/SIGTERM/pkill は一切不要。

### binder が壊れたときの復旧（再起動不要）

```bash
termux-sensor -c                       # → "Sensor cleanup successful!"
termux-sensor -s "Gravity" -n 1        # 動作確認
```

`-c` も応答しない場合：設定 → アプリ → Termux:API → **強制停止**（GUIから）

---

## GitHub リポジトリ

- URL: https://github.com/zabieru314/chair-logger.git
- ブランチ: main
- スマホ側パス: ~/chair_logger/chair_logger/
- 最新コミット: `a32f6d1`（日次サマリー追加）

---

## センサー仕様

- センサー名: `"Gravity Sensor"`（コードは `"gravity" in key.lower()` でマッチ）
- Android 15 + OPPO ColorOS：Termux がフォアグラウンド（画面オン）のみセンサーアクセス可
