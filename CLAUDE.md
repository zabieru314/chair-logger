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

## 現在の実装状態（最終更新 2026-05-09）

### 検知方式：30秒ポーリング + XYZ差分

Popen連続ストリームを廃止し、30秒ごとに `termux-sensor -n 1` で1点取得する方式に変更。

```
delta = |ΔX| + |ΔY| + |ΔZ|  （前回取得値との差分）
delta >= VARIANCE_THRESHOLD → 着席（即確定・1回で十分）
delta <  VARIANCE_THRESHOLD が DEBOUNCE_LEFT_SEC 秒継続 → 離席確定
```

実測値:
- 着席中 delta ≈ 0.07〜0.30（体重移動・呼吸でXYZが揺れる）
- 離席時（クッションドリフト完全静止後）delta ≈ 0.006〜0.009
- 閾値: `VARIANCE_THRESHOLD=0.07`

**閾値を0.07にした理由（2026-05-09）:**
机の上に置いたスマホを手で触ると delta=0.063 が発生して誤着席確定していた。
着席中の実測下限が 0.07 なので 0.07 に引き上げて解決。

**着席確定バグ修正（commit 233485c）:**
初期実装では2回連続スパイクが必要だったが、スパイクは2〜3分に1回程度しか来ないため永遠に着席確定しなかった。1回スパイクで即確定に変更して解決。

### 通知の2層構造

| 通知 | タイミング | 送信先 |
|------|-----------|--------|
| リアルタイム（着席/離席） | 状態確定ごと | デバッグチャンネル（WEBHOOK_URL） |
| 日次サマリー | 毎日22:00 | サマリーチャンネル（SUMMARY_WEBHOOK_URL） |

リアルタイム通知にバッテリー残量を追記（2026-05-09）:
```
[着席] 2026-05-09 10:23:45 作業を開始しました。  🔋82%
[離席] 2026-05-09 12:47:01 席を離れました。  🔋79%
```
- `hardware.get_battery_level()` → `notifier.notify_seated/notify_left(battery_level=...)` で渡す
- Termux環境以外では None → バッテリー表示なしで動作継続

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
VARIANCE_THRESHOLD=0.07     # 机誤検知対策で0.03→0.07に変更（2026-05-09）
DEBOUNCE_DELAY_SEC=5        # 着席確定（実質未使用）
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
- コミット履歴:
  - `a32f6d1` — 日次サマリー追加（2026-05-08）
  - `233485c` — 着席確定バグ修正（1スパイクで即確定、2026-05-09）
  - ※バッテリー通知・VARIANCE_THRESHOLD=0.07 はまだpush未確認（git push要）

---

## センサー仕様

- センサー名: `"Gravity Sensor"`（コードは `"gravity" in key.lower()` でマッチ）
- Android 15 + OPPO ColorOS：Termux がフォアグラウンド（画面オン）のみセンサーアクセス可

---

## 画面オン問題（解決済み・2026-05-09）

ColorOSが画面を強制的に消灯するため、センサーが動作しなくなる。

### 解決までの経緯（苦戦ポイント）

| 試したこと | 結果 | 理由 |
|-----------|------|------|
| Termuxから `settings put system screen_off_timeout` | 権限エラー（Failed transaction） | Termuxにはsystem settings書き込み権限がない |
| `adb connect 127.0.0.1:5555`（旧方式） | Connection refused | Android 11以降はUSBなしだとTCPポート5555が開かない |
| `adb tcpip 5555` → `adb connect` | `no devices/emulators found` | ADBデーモンにデバイスが未登録の状態では実行不可 |
| ワイヤレスデバッグでペアリング | `error: protocol fault (couldn't read status message): Success` | ADBバージョン互換性の問題。ペアリング自体は成功していた可能性があるが判断できず迷走 |
| WSL2から `sudo apt install adb` | sudoパスワード要求でブロック | WSL2はsudoにパスワードが必要 |
| platform-tools手動DL（Linux版） | `unzip`コマンドなし → Python展開で解決したが権限エラー | chmod +xで解消 |
| Windows側のadbを使う | **成功** | Android StudioのSDKに `C:\Users\zabie\AppData\Local\Android\Sdk\platform-tools\adb.exe` が存在 |

### 根本的な詰まりポイント

- **ワイヤレスデバッグかUSBか迷走**：ワイヤレスデバッグを試み続けたが、結局「USBデバッグで全然いい」というユーザー発言でシンプルな方法に気づいた
- **WSL2はUSBを直接認識しない**：USB接続してもWSL2側のadbでは見えない。Windows側の.exeをWSL2から呼び出すのが正解
- **設定が効いているか確認が難しい**：元々30分設定だったため「2〜3分のテストでは確認できない」。わざと1分（60000ms）に設定してUSBを抜いてテストする手順で解決

### 最終的な解決方法

```bash
# Windows側のadbをWSL2から実行
ADB=/mnt/c/Users/zabie/AppData/Local/Android/Sdk/platform-tools/adb.exe

# 画面タイムアウトを実質無効化（約596時間）
$ADB shell settings put system screen_off_timeout 2147483647

# 確認
$ADB shell settings get system screen_off_timeout  # → 2147483647
```

動作確認済み（2026-05-09）：
- ADBで60000（1分）に設定 → USB抜いて1分で画面オフを確認（設定が効いていることを確認）
- ADBで2147483647に再設定 → 維持されることを確認
- ColorOSによる自動リセットは発生しなかった

注意：**スマホを再起動すると値がリセットされる可能性あり**。再起動後はUSB繋いで再設定が必要。

### USB抜いても画面オフになる問題（追加調査 2026-05-09）

USB 抜いた直後に 1 分で画面が切れる事象が発生。原因は `power_save_screenoff_time_state=1`（省電力モード時の独自画面オフ）。

```bash
# 修正コマンド（USB繋いで実行済み）
$ADB shell settings put system power_save_screenoff_time_state 0
$ADB shell settings get system power_save_screenoff_time_state  # → 0
```

調査で分かったこと：
- `screen_off_timeout=2147483647` は維持されていた（ColorOSによるリセットなし）
- `oplus_customize_smart_apperceive_enabled=0`（スマート認識：無効）
- `oplus_customize_smart_apperceive_screen_lock=0`（裏返しでロック：無効）
- 「裏返しで画面オフ」のADB設定は見つからず → OPPO 設定UIの「便利な機能 > フリップ」で確認・無効化が必要

### バッテリー残量通知が表示されない問題（解決 2026-05-09）

`termux-battery-status` が Termux:API binder 経由で失敗 → 通知に `🔋` が表示されなかった。

修正: `src/utils/hardware.py` の `get_battery_level()` を `/sys/class/power_supply/battery/capacity` 直読み優先に変更。

```
/sys/class/power_supply/battery/capacity  ← 現在 87% を確認
/sys/class/power_supply/mtk-battery/capacity  ← フォールバック
```

Termux:API 不要・binder 状態に依存しないため安定動作する。

### 現在の画面オン維持のための設定（全部ADB適用済み）

```bash
$ADB shell settings put system screen_off_timeout 2147483647          # タイムアウト無効化
$ADB shell settings put system power_save_screenoff_time_state 0       # 省電力画面オフ無効
```

スマホ再起動時は上記 2 コマンドの再実行が必要。

### フリップ（裏返し）で画面がオフになる問題の調査結果（2026-05-09）

pocket_mode_enable / oplus_pocket_mode_enabled など ADB 設定キーは全て null（存在しない）。
OPPO ColorOS の近接センサーによる自動消灯はハードウェア/システムレベルで制御されており、
ADB settings コマンドでは無効化不可。

→ 解決策: **ScreenKeeper（Termux自己ADB）** を main.py に実装。
  25秒ごとに `adb -s localhost:5555 shell input keyevent 224`（KEYCODE_WAKEUP）を送信し、
  画面が消えても即座に復帰させる。

### ScreenKeeper のセットアップ手順

**PC側（再起動後に毎回実行）:**
```bash
$ADB tcpip 5555
```

**スマホ側（初回のみ）:**
```bash
pkg update && pkg install android-tools
adb connect localhost:5555
# → 承認ポップアップが出たら「常に許可」でOK
```

**スマホ再起動後:**
1. USB 繋いで `$ADB tcpip 5555`（PC側）
2. Termux で `adb connect localhost:5555`
3. `python main.py`

**注意:**
- スマホ再起動時は tcpip モードがリセットされる → USB 繋いで PC から再設定が必要
- `android-tools` がインストールされていない場合は ScreenKeeper が自動無効化（他機能は正常動作）
- センサーオフ（Sensors Off）は重力センサーも止まるため絶対に使わないこと

### スマホのADB接続情報

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

### settings書き込みコマンド

```bash
ADB=/mnt/c/Users/zabie/AppData/Local/Android/Sdk/platform-tools/adb.exe
$ADB shell settings put system screen_off_timeout 2147483647
$ADB shell settings get system screen_off_timeout  # → 2147483647 が返ればOK
```
