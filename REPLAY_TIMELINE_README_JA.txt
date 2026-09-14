RAD EDOPro v0.6 リプレイ内タイムライン（試作）

対応命令:
  PACE 50..150  : 以降のRAD再生速度を変更
  WAIT 0..600000: 指定ミリ秒だけ次の処理を遅延（SE/BGM自体は伸ばさない）
  RESET         : リプレイ内速度指定を解除し、手動Replay Paceへ戻す
  PAUSE         : 再生ボタンを押すまで停止

重要:
- 専用命令を埋め込んだ .yrpX は RAD EDOPro v0.6 で再生してください。
- 通常の .yrpX は従来どおり再生できます。
- Undo/途中ジャンプの高速追いつき中は WAIT/PAUSE を無視します。
  PACE/RESET は適用されるので、到達地点の速度状態は再現されます。
- +/-/100% を手動操作すると、その時点のタイムライン速度指定は解除されます。

使い方（Python 3）:

1) パケット番号を見る
   python rad_replay_timeline.py inspect input.yrpX

2) timeline.txt を作る
   例:
     before 120 pace 65
     before 120 wait 1200
     after 145 reset
     before 200 pause

   パケット番号は0始まりです。
   before N = N番パケットの直前
   after N  = N番パケットの直後

3) タイムラインを埋め込む
   python rad_replay_timeline.py apply input.yrpX timeline.txt output.yrpX

4) 全タイムライン命令を除去したい場合
   python rad_replay_timeline.py strip output.yrpX clean.yrpX

同じファイルへ再適用する場合、既存RAD命令は一度除去してから新しいtimeline.txtを適用します。
そのためinspectで表示される通常パケット番号は、タイムライン編集を繰り返してもずれません。
