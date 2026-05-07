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

### 正しい停止方法（2026-05-08 確定）

```bash
# Python コードから
import signal
proc.send_signal(signal.SIGINT)        # ← SIGINT が正解
proc.wait(timeout=5)
subprocess.run(["termux-sensor", "-c"])  # リスナー解放の念押し
```

SIGINT を受けると termux-sensor は以下を出力して安全に終了する：
```
^CCaught interrupt.. Finishing...
Performing sensor cleanup
Sensor cleanup successful!
```

### 壊れる操作（絶対禁止）

- `proc.terminate()` / `proc.kill()` → SIGTERM/SIGKILL → binder 破壊
- `pkill -f termux-sensor` → binder 破壊

### binder が壊れたときの復旧（再起動不要）

```bash
termux-sensor -c          # → "Sensor cleanup successful!" が出れば復旧
termux-sensor -s "Gravity" -n 1   # 動作確認
```

`-c` も応答しない場合：設定 → アプリ → アプリ管理 → Termux:API → **強制停止**（GUIから）

### calibrate.py の設計（実装済み）

フェーズごとに `subprocess.run -n 20`（10秒）の独立セッションを使い、
`finally` ブロックで `termux-sensor -c` を呼んでリスナーを毎回解放する。
これにより何度でも連続実行できる。

---

## 【検知方式】差分メトリクス（2026-05-08 変更）

Z軸の絶対値閾値はスマホの置き方で逆転する問題があったため、
**連続サンプル間の変化量**（差分メトリクス）方式に変更した。

### アルゴリズム

```
metric = mean(|ΔX[i] - ΔX[i-1]| + |ΔY[i] - ΔY[i-1]|) over 60 samples (30秒ウィンドウ)
metric >= VARIANCE_THRESHOLD → 着席（体の微動を検出）
metric <  VARIANCE_THRESHOLD → 離席（センサーが静止）
```

### 実測値（参考）
- 着席時 metric ≈ 0.10〜0.20（呼吸・体重移動でX/Yが揺れる）
- 離席時 metric ≈ 0.01〜0.015（クッションのゆっくりした変形ドリフト）
- デフォルト閾値: `VARIANCE_THRESHOLD=0.03`

### この方式のメリット
- スマホの置く角度・向きに依存しない
- クッション下・椅子横など置き場所を選ばない

---

## calibrate.py の使い方

```bash
# main.py が動いていないことを確認してから実行
cd ~/chair_logger/chair_logger
python calibrate.py
```

1. 着席状態で Enter → 3秒カウントダウン → 10秒収集 → 着席メトリクスが表示される
2. Enter を押してからその場を離れる → 5秒後に10秒収集 → 離席メトリクスが表示される
3. **着席メトリクス > 離席メトリクス** なら y で `.env` を自動更新
4. そのまま `python main.py` を起動できる（再起動不要）

※ デフォルト `VARIANCE_THRESHOLD=0.03` のまま `python main.py` を試してから
  必要なら calibrate.py でチューニングする方針でもよい。

---

## センサー仕様

### センサー名
- 正式名: `"Gravity Sensor"`（termux-sensor -l で確認）
- コマンド引数では `"gravity"` または `"Gravity Sensor"` で動作確認済み
- JSON キー: `"Gravity Sensor"` → コード側は `"gravity" in key.lower()` でマッチ

### Android 15 + OPPO 制限
- バックグラウンドでのセンサーアクセスはブロックされる
- Termux がフォアグラウンド（画面オン）の場合は動作する

---

## ファイル構成

```
main.py              エントリーポイント
calibrate.py         閾値キャリブレーション（差分メトリクス版）
src/core/sensor.py   センサー監視コア（差分メトリクス判定）
src/db/models.py     SQLite操作
src/utils/notifier.py Discord Webhook通知
src/utils/hardware.py 温度監視
src/web/app.py       Flask WebUI
.env                 設定値（VARIANCE_THRESHOLD, WEBHOOK_URL等）
```

## GitHub リポジトリ
- URL: https://github.com/zabieru314/chair-logger.git
- ブランチ: main
- スマホ側: ~/chair_logger/chair_logger/
- 最新コミット: `b0b65f0`（差分メトリクス方式への切り替え）
