# 503_着席検知システム

## 【絶対ルール】このプロジェクトは必ず Opus モデルで作業すること

このプロジェクトはセンサー・Android・Termux の複雑なデバッグが多いため、
必ず `--model opus` で起動した Claude Code で作業すること。

Sonnet で誤って開いた場合は即座に再起動する：
```
claude --model opus
```

## プロジェクト概要

古いAndroidスマホ（Termux環境）で着席・離席を自動検知してDiscordに通知するIoTシステム。

---

## 【最重要】Termux:API センサーの動作法則（2026-05-08 解明・確認済み）

### 壊れる条件（絶対にやってはいけない）

`termux-sensor -s gravity -d 500`（連続モード、-n なし）を起動した後、
**SIGTERM・SIGKILL・pkill で止めると binder 接続が永久に壊れる。**

壊れると：
- `termux-sensor -s "Gravity" -n 1` を含め一切のセンサーコマンドがタイムアウト
- Termux:API アプリを開き直しても復旧しない
- **電話の再起動だけが復旧手段**（または `am force-stop com.termux.api` が有効な場合あり）

### 壊れない条件

- `termux-sensor -s gravity -n 1`（有限モード）は何回呼んでも安全
- 連続モードの Popen は 1セッション中に **1回だけ** 起動し、途中で止めない
- Python プロセスが自然終了すれば子プロセスは SIGPIPE で安全に死ぬ（terminate 不要）

### calibrate.py の正しい設計（1セッション設計）

```
termux-sensor を1回だけ Popen 起動
  → バックグラウンドスレッドで全サンプルを (時刻, Z値) に蓄積し続ける
  → 着席フェーズ：開始時刻・終了時刻を記録（terminate しない）
  → 離席フェーズ：同じ Popen から継続（terminate しない）
  → Python exit → 子プロセスが SIGPIPE で自然死（明示的 terminate 不要）
  → 時間窓でサンプルを切り出して集計
```

途中で Popen を止め直すような変更は絶対に入れないこと。

---

## calibrate.py の使い方

```bash
# main.py が動いていないことを確認してから実行
cd ~/chair_logger/chair_logger
python calibrate.py
```

1. 着席状態で Enter → 3秒カウントダウン → 10秒収集
2. Enter を押してからその場を離れる → 5秒後に10秒収集
3. 結果が出たら y で .env を自動更新
4. そのまま `python main.py` を起動できる（再起動不要）

---

## センサー仕様

### センサー名
- 正式名: `"Gravity Sensor"`（termux-sensor -l で確認）
- コマンド引数では `"gravity"` または `"Gravity Sensor"` で動作確認済み
- JSON キー: `"Gravity Sensor"` → コード側は `"gravity" in key.lower()` でマッチ

### Z軸の値
- 水平置き（着席状態）: Z ≈ 9.635〜9.803（実測）
- 傾き（離席状態）: Z ≈ 2〜4（推定）
- Z_THRESHOLD デフォルト: 5.0（calibrate.py で実測値に更新可能）

### Android 15 + OPPO 制限
- バックグラウンドでのセンサーアクセスはブロックされる
- Termux がフォアグラウンド（画面オン）の場合は動作する

---

## ファイル構成

```
main.py              エントリーポイント
calibrate.py         閾値キャリブレーション（1セッション設計）
src/core/sensor.py   センサー監視コア（Z軸判定）
src/db/models.py     SQLite操作
src/utils/notifier.py Discord Webhook通知
src/utils/hardware.py 温度監視
src/web/app.py       Flask WebUI
.env                 設定値（Z_THRESHOLD, WEBHOOK_URL等）
```

## GitHub リポジトリ
- URL: https://github.com/zabieru314/chair-logger.git
- ブランチ: main
- スマホ側: ~/chair_logger/chair_logger/
