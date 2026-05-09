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

- スマホ: OPPO（ColorOS 15 / Android 15）
- Termux + Termux:API でセンサー読み取り
- Discord Webhook でリアルタイム通知 + 日次サマリー

---

## 現在の実装状態（最終更新 2026-05-09）

### 検知方式：30秒ポーリング + XYZ差分

Popen連続ストリームを廃止し、30秒ごとに `termux-sensor -s gravity -n 1` で1点取得する方式。

```
delta = |ΔX| + |ΔY| + |ΔZ|  （前回取得値との差分）
delta >= VARIANCE_THRESHOLD → 着席（即確定・1回で十分）
delta <  VARIANCE_THRESHOLD が DEBOUNCE_LEFT_SEC 秒継続 → 離席確定
```

実測値:
- 着席中（静止）delta ≈ 0.002〜0.038（センサーノイズレベル）
- 着席中（動作）delta ≈ 0.05〜0.30（体重移動・呼吸でXYZが揺れる）
- 離席時 delta ≈ 0.002〜0.010
- 閾値: `VARIANCE_THRESHOLD=0.05`

**閾値の変遷:**
- 0.03 → 0.07（2026-05-09）: 机の上のスマホを手で触ると delta=0.063 が誤着席確定していたため引き上げ
- 0.07 → 0.05（2026-05-09）: 静止着席時の実測最大値が0.038で、0.07は高すぎて誤離席が頻発。0.05に下げて感度改善。
- 0.05 → 0.04（2026-05-09）: 静止着席中にΔmax=0.049を記録。0.05を僅かに下回り誤離席確定したため0.04に引き下げ。⚠️ 机への軽い接触（0.063程度）で誤着席のリスクは残る。

**着席確定バグ修正（commit 233485c）:**
初期実装では2回連続スパイクが必要だったが、スパイクは2〜3分に1回程度しか来ないため永遠に着席確定しなかった。1回スパイクで即確定に変更して解決。

### 通知の3層構造

| 通知 | タイミング | 送信先 |
|------|-----------|--------|
| リアルタイム（起動/着席/離席） | 状態確定ごと | デバッグチャンネル（WEBHOOK_URL） |
| 日次サマリー | 毎日22:00（手動トリガー可） | サマリーチャンネル（SUMMARY_WEBHOOK_URL） |
| バッテリーアラート | 着席/離席時 + 30分ごと | サマリーチャンネル（SUMMARY_WEBHOOK_URL） |

通知例（バッテリー表示 + Δ値 + 目標進捗つき）:
```
[起動] 2026-05-09 18:07:09 着席検知システムを起動しました。  🔋88%  ⏱ 0分/6時間（0%）
[着席] 2026-05-09 10:23:45 作業を開始しました。  🔋82%  Δ: 0.696  ⏱ 1時間23分/6時間（23%）
[離席] 2026-05-09 12:47:01 席を離れました。  🔋79%  Δmax: 0.038  ⏱ 3時間10分/6時間（52%）
```

Δ値の意味:
- `[着席]` の `Δ:` → 着席確定を引き起こした単発スパイクのdelta値
- `[離席]` の `Δmax:` → 離席待機期間（10分）中に観測した最大delta値

⏱ 目標進捗: `DAILY_GOAL_MINUTES=360` で設定（0なら非表示）

バッテリー取得の優先順位（hardware.py）:
1. `/sys/class/power_supply/battery/capacity`（直読み・Termux:API不要）
2. `/sys/class/power_supply/mtk-battery/capacity`（MTK系フォールバック）
3. `/system/bin/cmd battery get level`
4. `termux-battery-status`（Termux:API 経由・最終手段・タイムアウト15秒）

**バッテリー取得の経緯（2026-05-09）:**
`termux-battery-status` が Termux:API の binder 経由で失敗し続けていた。
`/sys/class/power_supply/battery/capacity` は 0444（world-readable）で ADB から 88% を確認。
Termux ユーザー（untrusted_app_27）からの読み取りも SELinux ログに拒否なし → 動作確認済み。

サマリー例：
```
📊 着席サマリー 5月8日（木）
09:15 〜 11:45  （2時間30分）
13:00 〜 15:30  （2時間30分）
合計着席: 5時間0分
```

### バッテリーアラート（2026-05-09追加）

閾値：20% / 15% / 10%（各1回のみ・再起動でリセット）

```
⚠️ バッテリー残量が 20% 以下になりました（現在 19%）
```

チェックタイミング：着席/離席確定時 + 30分ごと（毎ポーリング30秒はやりすぎのため）

実装ポイント（sensor.py）：
- `SensorConfig.summary_webhook_url` を追加
- `SensorMonitor._alerted_thresholds: set[int]` でメモリ管理
- `SensorMonitor._last_battery_check: float` で30分タイマー管理
- `_commit_state()` でバッテリー取得済みの値を再利用して `_check_battery_alert(level)` に渡す

### サマリー手動送信（2026-05-09追加）

Flask に `POST /api/summary/send` エンドポイントを追加。PCのターミナルから即時送信できる。

```bash
# 今日のサマリーを送信
curl -X POST http://192.168.10.104:8080/api/summary/send

# 日付指定（過去分）
curl -X POST "http://192.168.10.104:8080/api/summary/send?date=2026-05-08"
```

レスポンス例：
```json
{"sent": true, "date": "2026-05-09", "total_minutes": 124, "periods": 10}
```

末尾に `〜` がつく期間（例: `17:56 〜 18:48〜`）は「まだ着席中」を意味する（まだ `left` が記録されていない）。

---

## デプロイ方針（重要）

**PC側で編集・push → スマホ側は `git pull` + 再起動のみ。**
スマホで直接ファイルを編集しない。

### 設定ファイル構成

| ファイル | git管理 | 用途 |
|--------|--------|------|
| `.env.config` | ✅ 管理対象 | 設定値全般（VARIANCE_THRESHOLD等） |
| `.env` | ❌ gitignore | WEBHOOK_URL のみ（スマホのみに存在） |

**⚠️ 注意: スマホの `.env` に古い `VARIANCE_THRESHOLD=0.03` が残っていると `.env.config` の 0.07 を上書きしてしまう。**
`.env` には `WEBHOOK_URL` と `SUMMARY_WEBHOOK_URL` だけ残して他は削除すること。

設定変更手順：
1. PC側で `.env.config` を編集
2. `git push`
3. スマホで `git pull` → `python main.py` 再起動

### 現在の `.env.config` 設定値

```
SUMMARY_WEBHOOK_URL=https://discord.com/api/webhooks/1502313121769848892/...
SUMMARY_TIME=22:00
VARIANCE_THRESHOLD=0.04     # 0.03→0.07→0.05→0.04と調整（静止着席での誤離席対策）
DAILY_GOAL_MINUTES=360      # 1日の目標着席分数（0=表示なし）
DEBOUNCE_DELAY_SEC=5        # 着席確定（実質未使用）
DEBOUNCE_LEFT_SEC=600       # 離席確定（10分）
SENSOR_POLL_INTERVAL_SEC=30 # ポーリング間隔
MAX_TEMP_CELSIUS=40.0
COOLDOWN_SLEEP_SEC=300
TEMP_CHECK_INTERVAL_SEC=60
FLASK_HOST=0.0.0.0
FLASK_PORT=8080
```

---

## スマホ側での操作

### 通常の再起動手順

```bash
cd ~/chair_logger/chair_logger
git pull
# Ctrl+C で旧プロセスを止めてから
python main.py
```

### ScreenKeeper のセットアップ（初回のみ・フリップ対策）

**PC側（再起動後に毎回実行）:**
```bash
ADB=/mnt/c/Users/zabie/AppData/Local/Android/Sdk/platform-tools/adb.exe
$ADB tcpip 5555
```

**スマホ側（初回のみ）:**
```bash
pkg update && pkg install android-tools
adb connect localhost:5555
# → 承認ポップアップが出たら「常に許可」でOK
```

**スマホ再起動後の手順:**
1. USB 繋ぐ
2. PC から `$ADB tcpip 5555`
3. Termux で `adb connect localhost:5555`
4. `python main.py`

---

## ファイル構成

```
main.py               エントリーポイント + SummaryScheduler + ScreenKeeper
calibrate.py          閾値キャリブレーション（-n 20 方式）
.env.config           設定値（git管理対象）
.env                  WEBHOOK_URL のみ（gitignore・スマホのみ）
src/core/sensor.py    30秒ポーリング + XYZ差分判定 + バッテリーアラート
src/db/models.py      SQLite操作 + calc_daily_summary
src/utils/notifier.py Discord通知 + notify_summary
src/utils/hardware.py バッテリー取得（/sys直読み）+ 温度監視
src/web/app.py        Flask WebUI（ポート8080）+ /api/summary/send
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
- コミット履歴:
  - `a32f6d1` — 日次サマリー追加（2026-05-08）
  - `233485c` — 着席確定バグ修正（1スパイクで即確定、2026-05-09）
  - `ba5de3a` — バッテリー通知・画面維持の安定性改善（2026-05-09）
  - `03aa183` — バッテリー取得多段フォールバック・起動通知にバッテリー追加（2026-05-09）
  - `fa9c138` — ScreenKeeper 追加（adb localhost:5555 経由の画面常時点灯、2026-05-09）
  - `6497a94` — VARIANCE_THRESHOLD を 0.03→0.07 に修正（git push 漏れを解消、2026-05-09）
  - `d416c4c` — 着席・離席通知にバッテリー残量を追加（sensor.py コミット漏れ修正、2026-05-09）
  - `7244f76` — サマリー即時送信エンドポイント追加（POST /api/summary/send、2026-05-09）
  - `da21e64` — バッテリー残量アラート追加（20%/15%/10%、サマリーch送信、2026-05-09）
  - `2acda9c` — バッテリーアラートを30分ごと+着席/離席時のみに変更（2026-05-09）
  - `a353186` — 閾値0.07→0.05・debounce 300→600秒・通知にΔmax追加（2026-05-09）
  - `0b11a1d` — VARIANCE_THRESHOLD 0.05→0.04（静止着席Δmax=0.049の誤離席対策、2026-05-09）
  - `6bd4ed6` — 着席目標進捗表示（起動/着席/離席通知に⏱追加、2026-05-09）

---

## センサー仕様

- センサー名: `"Gravity Sensor"`（コードは `"gravity" in key.lower()` でマッチ）
- Android 15 + OPPO ColorOS：Termux がフォアグラウンド（画面オン）のみセンサーアクセス可

---

## 画面オン問題（解決済み・2026-05-09）

ColorOSが画面を強制的に消灯するため、センサーが動作しなくなる問題を段階的に解決した。

### 問題1: 画面タイムアウト（解決済み）

**症状:** 数分で画面が消える  
**原因:** デフォルトのスクリーンタイムアウト設定  
**解決:**
```bash
$ADB shell settings put system screen_off_timeout 2147483647
```

### 問題2: USB抜いたら1分で画面オフ（解決済み）

**症状:** USB を抜いた直後から 1 分で画面が消える  
**原因:** `power_save_screenoff_time_state=1`（OPPO独自の省電力画面オフ）  
**解決:**
```bash
$ADB shell settings put system power_save_screenoff_time_state 0
```

### 問題3: 裏返しで画面がオフになる可能性（ScreenKeeper で対策済み）

**調査結果:**
- `pocket_mode_enable` / `oplus_pocket_mode_enabled` など関連キーは全て null（存在しない）
- `gesture_turn_over_to_mute_enable` も null
- OPPO ColorOS の近接センサーによる自動消灯は ADB settings では制御不可
- センサーオフ（Sensors Off）は重力センサーも止まるため使用禁止

**テスト結果（2026-05-09）:** 裏返しても画面が消えないことを確認。  
ADB 設定（screen_off_timeout + power_save_screenoff_time_state）だけで十分だった可能性が高い。  
ScreenKeeper は万一の安全網として稼働中（25秒ごとに KEYCODE_WAKEUP 送信）。

### 解決経緯の苦戦ポイント（記事用）

| 試したこと | 結果 | 理由 |
|-----------|------|------|
| Termuxから `settings put system screen_off_timeout` | 権限エラー（Failed transaction） | Termuxにはsystem settings書き込み権限がない |
| `adb connect 127.0.0.1:5555`（旧方式） | Connection refused | Android 11以降はUSBなしだとTCPポート5555が開かない |
| `adb tcpip 5555` → `adb connect` | `no devices/emulators found` | ADBデーモンにデバイスが未登録の状態では実行不可 |
| ワイヤレスデバッグでペアリング | `error: protocol fault (couldn't read status message): Success` | ADBバージョン互換性の問題。ペアリング自体は成功していた可能性があるが判断できず迷走 |
| WSL2から `sudo apt install adb` | sudoパスワード要求でブロック | WSL2はsudoにパスワードが必要 |
| platform-tools手動DL（Linux版） | `unzip`コマンドなし → Python展開で解決したが権限エラー | chmod +xで解消 |
| Windows側のadbを使う | **成功** | Android StudioのSDKに `C:\Users\zabie\AppData\Local\Android\Sdk\platform-tools\adb.exe` が存在 |
| `pocket_mode_enable`等のADB設定 | 全て null（設定キーが存在しない） | OPPO ColorOS 固有機能はADB settings非対応 |
| `power_save_screenoff_time_state=0` | **成功** | OPPO独自の省電力画面オフを無効化できた |
| ScreenKeeper（自己ADB方式） | 動作確認済み | Termuxからlocalhost:5555に接続してKEYCODE_WEAKUPを定期送信 |

### 根本的な詰まりポイント

- **ワイヤレスデバッグかUSBか迷走**: ワイヤレスデバッグを試み続けたが、「USBデバッグで全然いい」という発言でシンプルな方法に気づいた
- **WSL2はUSBを直接認識しない**: USB接続してもWSL2側のadbでは見えない。Windows側の.exeをWSL2から呼び出すのが正解
- **設定が効いているか確認が難しい**: 元々30分設定だったため「2〜3分のテストでは確認できない」。わざと1分（60000ms）に設定してUSBを抜いてテストする手順で解決
- **`power_save_screenoff_time_state` の存在に気づくまで時間がかかった**: `screen_off_timeout=2147483647` を設定済みなのに1分でオフになる原因がわからなかった。全 system 設定を grep して発見

### 再起動後の画面設定コマンド（毎回必要）

```bash
ADB=/mnt/c/Users/zabie/AppData/Local/Android/Sdk/platform-tools/adb.exe
$ADB shell settings put system screen_off_timeout 2147483647
$ADB shell settings put system power_save_screenoff_time_state 0
$ADB tcpip 5555   # ScreenKeeper用（スマホ側で adb connect localhost:5555 も必要）
```

---

## スマホのADB接続情報

- **接続方式**: USBデバッグ（ワイヤレスデバッグは使わない）
- **adbパス**: `/mnt/c/Users/zabie/AppData/Local/Android/Sdk/platform-tools/adb.exe`（WSL2から実行）
- **デバイスID**: `H6RGY9W8GEUWXSXK`
- **スマホIPアドレス**: `192.168.10.104`（参考）

### ADB接続手順（毎回）

1. スマホのUSBデバッグがオンであることを確認
2. USBケーブルでPCに繋ぐ
3. WSL2から確認：
   ```
   /mnt/c/Users/zabie/AppData/Local/Android/Sdk/platform-tools/adb.exe devices
   ```
