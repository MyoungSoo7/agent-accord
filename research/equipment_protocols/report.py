"""results.json -> results.md (tables) + figures/*.png (matplotlib, Agg)."""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# reference palette (light): categorical slots in fixed order, recessive chrome
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK, "axes.titlesize": 10,
    "axes.labelsize": 9, "xtick.color": MUTED, "ytick.color": INK2, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False, "legend.fontsize": 8,
    "font.size": 9, "lines.linewidth": 2,
})

Q1_LABELS = {
    "secs": "SECS-II S6F11/F12 (HSMS)",
    "opcua": "OPC UA data change, 1 event/Publish",
    "opcua_batch10": "OPC UA data change, batched",
    "json_q0": "MQTT JSON QoS0",
    "json_q1": "MQTT JSON QoS1",
    "spb_alias_q0": "Sparkplug B alias QoS0",
    "spb_name_q0": "Sparkplug B name QoS0",
    "spb_alias_q1": "Sparkplug B alias QoS1",
    "spb_alias_nots_q0": "Sparkplug B alias, no metric ts",
}
Q2_LABELS = {
    "secs_direct": "SECS/GEM direct",
    "opcua": "OPC UA subscription",
    "mqtt_qos0": "MQTT QoS0",
    "mqtt_qos1": "MQTT QoS1",
    "gateway": "SECS/GEM→gateway→MQTT",
    "gateway_ack_receipt": "…gateway, S6F12 before PUBACK",
    "mqtt_qos0_nagle": "MQTT QoS0, Nagle on",
}
Q3A_LABELS = {
    "mqtt_qos0_persistent": "MQTT QoS0, persistent",
    "mqtt_qos1_clean": "MQTT QoS1, clean session",
    "mqtt_qos1_persistent": "MQTT QoS1, persistent",
    "secs_nospool": "SECS/GEM, no spool",
    "secs_spool": "SECS/GEM, spool + S6F23",
}


# ------------------------------------------------------------------------------ formatting

def _num(v, d: int) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if d == 0:
        return f"{v:,.0f}"
    return f"{v:,.{d}f}"


def fs(s: dict | None, d: int = 1, spread_too: bool = True) -> str:
    """median [min–max] (or just the value when min == max)."""
    if not s or s.get("n", 0) == 0:
        return "–"
    med = _num(s["median"], d)
    if not spread_too or _num(s["min"], d) == _num(s["max"], d):
        return med
    return f"{med} [{_num(s['min'], d)}–{_num(s['max'], d)}]"


def table(header: list[str], rows: list[list[str]], align: str | None = None) -> str:
    align = align or ("l" + "r" * (len(header) - 1))
    sep = ["---:" if a == "r" else "---" for a in align]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


# ------------------------------------------------------------------------------ sections

def q1_md(q1: dict) -> str:
    out = ["## Q1. 이벤트 하나의 와이어 비용 (TCP 페이로드 바이트)", "",
           f"반복 {q1['summary']['secs']['1']['reps']}회의 중앙값 [최소–최대]. 정상 상태 구간에서 이벤트 "
           f"{q1['events_per_run']}개(OPC UA 묶음은 {2 * q1['events_per_run']}개)를 보낸 바이트를 이벤트 수로 나눴다. "
           "이벤트 방향 = 장비/서버/발행자 → 소비자. MQTT 는 발행자→브로커 구간이고, 브로커→구독자 구간은 따로 적었다. "
           "설정 바이트는 양방향 합.", ""]
    rows = []
    for v in q1["summary"]:
        for n in map(str, q1["ns"]):
            s = q1["summary"][v][n]
            first = s["legs"].get("link") or s["legs"]["pub"]
            sub = s["legs"].get("sub")
            rows.append([
                Q1_LABELS.get(v, v), n, fs(first["fwd_per_event"], 1), fs(first["rev_per_event"], 1),
                fs(sub["fwd_per_event"], 1) if sub else "–", fs(sub["rev_per_event"], 1) if sub else "–",
                fs(first["setup_connect"], 0), fs(first["setup_app"], 0), fs(s["app_payload_per_event"], 1),
            ])
    out.append(table(["구성", "N", "이벤트 방향 B/ev", "역방향 B/ev", "브로커→구독자 B/ev", "구독자→브로커 B/ev",
                      "연결 설정 B", "응용 설정 B", "응용 페이로드 B/ev"], rows, "lrrrrrrrr"))
    out += ["", "N=10 → N=50 사이 변수 하나당 추가 바이트 (이벤트 방향, 중앙값 기준):", ""]
    rows = []
    for v in q1["summary"]:
        s10, s50 = q1["summary"][v]["10"], q1["summary"][v]["50"]
        leg = "link" if "link" in s10["legs"] else "pub"
        d = (s50["legs"][leg]["fwd_per_event"]["median"] - s10["legs"][leg]["fwd_per_event"]["median"]) / 40
        rows.append([Q1_LABELS.get(v, v), f"{d:.1f}"])
    out.append(table(["구성", "B/변수"], rows))
    opc = [r for r in q1["runs"] if r["variant"].startswith("opcua")]
    if opc:
        out += ["", "OPC UA 세부: PublishResponse 하나에 실린 이벤트 수 (정상 상태, 중앙값), 서버가 돌려준 값:", ""]
        rows = []
        for v in ("opcua", "opcua_batch10"):
            for n in q1["ns"]:
                rs = [r for r in opc if r["variant"] == v and r["n"] == n]
                epr = sorted(r["events_per_response"] for r in rs)[len(rs) // 2]
                sub = rs[0]["subscription"]
                rows.append([Q1_LABELS[v], str(n), f"{epr:.2f}", f"{sub['revised_publishing_interval']:g}",
                             str(sub["revised_queue_size"]), str(sub["revised_sampling_interval"])])
        out.append(table(["구성", "N", "이벤트/응답", "revised publishing ms", "revised queueSize",
                          "revised sampling ms"], rows))
    if q1.get("incomplete"):
        out += ["", f"불완전 실행: {q1['incomplete']}"]
    return "\n".join(out)


def q2_md(q2: dict, reps: int) -> str:
    out = ["## Q2. 루프백 지연과 처리량", "",
           f"N={q2['n']} 변수(+seq, 생성시각). 반복 {reps}회 각각의 백분위수를 구한 뒤 반복 간 중앙값 [최소–최대]. "
           "지연 = 소비자 응용 코드 도착 시각 − 생산자 이벤트 생성 시각 (같은 호스트 CLOCK_MONOTONIC). "
           "r10/r100/r1000 = 평균 10/100/1000 ev/s 포아송 도착, burst = 3000 이벤트를 쉬지 않고 생성. "
           "절대값은 이 파이썬 구현들을 한 대에서 돌린 값이지 프로토콜 한계가 아니다.", ""]
    rows = []
    for name in q2["configs"]:
        if name not in q2["summary"]:
            continue
        for label, s in q2["summary"][name].items():
            if label == "burst" or label.startswith("_"):
                continue
            rows.append([Q2_LABELS.get(name, name), label, fs(s["p50"], 2), fs(s["p95"], 2), fs(s["p99"], 2),
                         fs(s["delivered_rate"], 0), str(s["lost_total"]), str(s["duplicates_total"]),
                         _num(s["samples_per_rep"]["median"], 0)])
    out.append(table(["구성", "부하", "p50 ms", "p95 ms", "p99 ms", "전달률 ev/s", "손실(합)", "중복(합)",
                      "표본/반복"], rows, "llrrrrrrr"))
    loads = {n: q2["summary"][n].get("_loadavg_1m") for n in q2["summary"]}
    if any(loads.values()):
        out += ["", "반복 시작 시 1분 부하 평균(4 vCPU, 같은 기계의 다른 작업 포함): " + ", ".join(
            f"{Q2_LABELS.get(n, n)} {fs(v, 2)}" for n, v in loads.items() if v) + ". 반복 순서는 "
            "반복 우선(반복 0 의 모든 구성 → 반복 1 …)이라 부하 변동이 구성마다 고르게 퍼진다."]
    out += ["", "포화 처리량 (burst 3000 이벤트, 소비자 쪽 전달률) — 이 구현 조합의 최대 지속 가능 속도로 본다. "
            "burst 의 지연은 3000 개가 한꺼번에 생겨 줄 선 시간을 포함한다:", ""]
    rows = []
    for name in q2["configs"]:
        s = q2["summary"].get(name, {}).get("burst")
        if not s:
            continue
        rows.append([Q2_LABELS.get(name, name), fs(s["delivered_rate"], 0), fs(s["p50"], 0), fs(s["p99"], 0),
                     f"{s['lost_total']} / {s['expected_per_rep'] * s['reps']}",
                     str(q2.get("broker", {}).get(name, {}).get("dropped_notices", "–"))])
    out.append(table(["구성", "전달률 ev/s", "p50 ms", "p99 ms", "손실/생성(합)", "브로커 drop 알림"], rows))
    return "\n".join(out)


def q3_md(q3: dict, reps: int) -> str:
    a, b, c = q3["a_outage"], q3["b_coalescing"], q3["c_ndeath"]
    out = ["## Q3a. 소비자 정지 중 손실·중복", "",
           f"{a['rate']} ev/s 로 {a['duration_s']:g} 초 스트림, t={a['kill_at_s']:g} s 에 소비자 SIGKILL, "
           f"t={a['restart_at_s']:g} s 에 새 소비자 시작(같은 client id / 같은 호스트 역할). 반복 {reps}회 "
           "(각자 독립 브로커·프로세스로 동시 실행). 손실·중복·역순은 두 소비자의 기록을 합쳐 seq 로 셌다.", ""]
    rows = []
    for name in a["configs"]:
        s = a["summary"][name]
        rows.append([Q3A_LABELS.get(name, name), fs(s["lost"], 0), f"{s['lost_total']} / {s['expected_total']}",
                     str(s["duplicates_total"]), str(s["out_of_order_total"]), fs(s["outage_s"], 2),
                     fs(s["backlog_drain_ms"], 0) if s["lost_total"] == 0 else "–"])
    out.append(table(["구성", "손실/반복", "손실/생성(합)", "중복(합)", "역순(합)", "실제 정지 s",
                      "새 소비자 준비 후 밀린 분량 전달 ms"], rows))
    out += ["", "## Q3b. OPC UA 데이터 변경 병합 (queueSize)", "",
            f"한 변수를 {b['period_s'] * 1000:g} ms 간격으로 {b['count']}번 바꿈(0..{b['count'] - 1}). publishing interval "
            f"{b['publishing_interval_ms']:g} ms, discardOldest=true. 반복 {reps}회, 매 반복에서 네 구독이 같은 쓰기를 본다.", ""]
    rows = []
    for q in b["queue_sizes"]:
        s = b["summary"][str(q)]
        rows.append([str(q), str(s["revised_queue_size"]), fs(s["distinct"], 0), fs(s["vanished_pct"], 1),
                     fs(s["publish_responses"], 0), fs(s["values_per_response_max"], 0), fs(s["write_rate"], 0)])
    out.append(table(["요청 queueSize", "서버 revised", "받은 값(/1000)", "사라진 값 %", "데이터 응답 수",
                      "응답당 최대 값 수", "실제 쓰기 속도 /s"], rows))
    out += ["", "## Q3c. Sparkplug B NDEATH(Last Will)와 재탄생", "",
            f"게이트웨이(엣지 노드)가 {c['rate']} ev/s 로 DDATA 를 내던 중 t={c['fail_at_s']:g} s 에 장애. "
            "crash = SIGKILL(OS 가 소켓을 닫음), hang = SIGSTOP(소켓은 열린 채 침묵). hang 에서는 NDEATH 를 본 뒤 "
            f"멈춘 프로세스를 죽이고 새로 띄웠다. 장비는 스풀링을 켜 둠. 반복 {reps}회 (hang 은 {c.get('stagger_s', 0):g} 초씩 "
            "엇갈려 동시 실행).", ""]
    rows = []
    for name, s in c["summary"].items():
        rows.append([name, s["signal"], str(s["keepalive_s"]), fs(s["ndeath_after_fail_ms"], 1),
                     fs(s["nbirth_after_restart_ms"], 0), "예" if s["bdseq_match_all"] else "아니오",
                     f"{s['lost_total']} / {s['expected_total']}", str(s["duplicates_total"]),
                     fs(s["ddata_between_ndeath_and_nbirth"], 0)])
    out.append(table(["시나리오", "신호", "keepalive s", "장애→NDEATH ms", "재시작→NBIRTH ms",
                      "NDEATH bdSeq = 직전 NBIRTH", "손실/생성(합)", "중복(합)", "NDEATH~NBIRTH 사이 DDATA"],
                     rows, "llrrrlrrr"))
    probe = c.get("keepalive_probe")
    if probe:
        out += ["", f"침묵 클라이언트 탐침: CONNECT(Will 포함) 후 아무것도 보내지 않는 원시 소켓 {c['probe_per_k']}개씩, "
                "같은 브로커에 동시 접속. CONNECT 후 Will 이 구독자에 도착하기까지의 시간:", ""]
        rows = [[k, f"{v['one_and_half_k']:g}", fs(v["detect_s"], 3), ", ".join(f"{x:.3f}" for x in v["samples"])]
                for k, v in probe.items()]
        out.append(table(["keepalive K s", "1.5×K s", "검출 s", "표본 s"], rows))
    d = q3.get("d_gateway_ack")
    if d:
        out += ["", "## Q3d. 게이트웨이가 S6F12 를 언제 보내는가", "",
                f"{d['rate']} ev/s 스트림. t={d['blackhole_at_s']:g} s 에 게이트웨이→브로커 구간을 블랙홀(릴레이가 양방향을 "
                f"버리고 소켓은 유지), t={d['kill_at_s']:g} s 에 게이트웨이 SIGKILL 과 함께 구간 복구, t={d['restart_at_s']:g} s "
                f"에 새 게이트웨이. 장비는 두 경우 모두 스풀링을 켜 둠. 반복 {reps}회 (동시 실행).", ""]
        rows = []
        ko = {"ack_receipt": "S6F11 을 디코드하자마자 S6F12", "ack_puback": "브로커 PUBACK(QoS1)을 받은 뒤 S6F12"}
        for name, desc in d["configs"].items():
            desc = ko.get(name, desc)
            sm = d["summary"][name]
            rows.append([name, desc, fs(sm["lost"], 0), f"{sm['lost_total']} / {sm['expected_total']}",
                         str(sm["duplicates_total"]), fs(sm["blackhole_s"], 2)])
        out.append(table(["구성", "설명", "손실/반복", "손실/생성(합)", "중복(합)", "블랙홀 s"], rows, "llrrrr"))
    return "\n".join(out)


def meta_md(r: dict) -> str:
    m = r["meta"]
    pk = ", ".join(f"{k} {v}" for k, v in m["packages"].items())
    t = r["timing_s"]
    la = r.get("loadavg", {})
    load_line = "- 1분 부하 평균(4 vCPU, 다른 작업 포함) 구간 시작→끝: " + ", ".join(
        f"{k} {v['before'][0]:.2f}→{v['after'][0]:.2f}" for k, v in la.items()) if la else ""
    return "\n".join([
        "# 결과 (자동 생성)", "",
        f"`run_all.py` 가 {m['started_utc']} 에 만든 표다. 그림은 `figures/`.", "",
        f"- 환경: Python {m['python']}, {m['platform']}, {m['cpus']} vCPU ({m['cpu_model']})",
        f"- 브로커: {m['mosquitto']}",
        f"- 패키지: {pk}",
        f"- 반복: 구성마다 {r['reps']}회. 소요: " + ", ".join(f"{k} {v} s" for k, v in t.items()),
        load_line,
        "- 실패 후 재시도한 반복: " + ("없음" if not r.get("failures") else
                                 "; ".join(f"{f['label']} (시도 {f['attempt']}: {f['error'][:80]})" for f in r["failures"])),
    ])


# ------------------------------------------------------------------------------ figures

def _save(fig, path: Path) -> None:
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def fig_q1(q1: dict, path: Path) -> None:
    variants = list(q1["summary"])
    fig, axes = plt.subplots(1, len(q1["ns"]), figsize=(11, 4.2), sharey=True)
    for ax, n in zip(axes, map(str, q1["ns"])):
        fwd, rev = [], []
        for v in variants:
            legs = q1["summary"][v][n]["legs"]
            leg = legs.get("link") or legs["pub"]
            fwd.append(leg["fwd_per_event"]["median"])
            rev.append(leg["rev_per_event"]["median"])
        y = list(range(len(variants)))[::-1]
        ax.barh(y, fwd, height=0.55, color=S1, label="event direction")
        ax.barh(y, rev, left=[f + max(f * 0.01, 2) for f in fwd], height=0.55, color=S2,
                label="reverse (ack / request)")
        xmax = max(f + r for f, r in zip(fwd, rev))
        for yi, f, r in zip(y, fwd, rev):
            ax.text(f + r + xmax * 0.03, yi, f"{f:.0f}" + (f" + {r:.0f}" if r else ""), va="center",
                    fontsize=7.5, color=INK2)
        ax.set_xlim(0, xmax * 1.35)
        ax.set_title(f"N = {n} variables (+2)")
        ax.set_xlabel("bytes per event (TCP payload)")
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(list(range(len(variants)))[::-1], [Q1_LABELS.get(v, v) for v in variants])
    axes[-1].legend(loc="lower right")
    fig.suptitle("Q1  Wire cost per equipment event (steady state, median of repetitions; MQTT = publisher→broker leg)",
                 fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    _save(fig, path)


def fig_q2_latency(q2: dict, path: Path, reps: int) -> None:
    rates = ["r10", "r100", "r1000"]
    names = [n for n in q2["configs"] if n in q2["summary"]]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), sharey=True)
    for ax, rate in zip(axes, rates):
        y = list(range(len(names)))[::-1]
        for yi, name in zip(y, names):
            s = q2["summary"][name].get(rate)
            if not s:
                continue
            p50, p99 = s["p50"]["median"], s["p99"]["median"]
            ax.plot([p50, p99], [yi, yi], color=AXIS, linewidth=2, zorder=1)
            ax.scatter([p50], [yi], s=40, color=S1, zorder=3, edgecolors=SURFACE, linewidths=1.5,
                       label="p50" if yi == y[0] else None)
            ax.scatter([p99], [yi], s=40, color=S2, zorder=3, edgecolors=SURFACE, linewidths=1.5,
                       label="p99" if yi == y[0] else None)
            ax.text(p99 * 1.25, yi, f"{p99:.1f}", va="center", fontsize=7.5, color=INK2)
        ax.set_xscale("log")
        top = max(q2["summary"][n][r]["p99"]["median"] for n in names for r in rates if r in q2["summary"][n])
        ax.set_xlim(0.1, top * 4)
        ax.set_title(f"{rate[1:]} ev/s offered (Poisson)")
        ax.set_xlabel("end-to-end latency, ms (log)")
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(list(range(len(names)))[::-1], [Q2_LABELS.get(n, n) for n in names])
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=2, bbox_to_anchor=(0.99, 1.0))
    fig.suptitle(f"Q2  Loopback latency, median of {reps} repetitions (Python implementations on one host)",
                 fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    _save(fig, path)


def fig_q2_throughput(q2: dict, path: Path) -> None:
    names = [n for n in q2["configs"] if "burst" in q2["summary"].get(n, {})]
    med = [q2["summary"][n]["burst"]["delivered_rate"]["median"] for n in names]
    lo = [m - q2["summary"][n]["burst"]["delivered_rate"]["min"] for n, m in zip(names, med)]
    hi = [q2["summary"][n]["burst"]["delivered_rate"]["max"] - m for n, m in zip(names, med)]
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    y = list(range(len(names)))[::-1]
    ax.barh(y, med, height=0.5, color=S1)
    ax.errorbar(med, y, xerr=[lo, hi], fmt="none", ecolor=INK2, elinewidth=1, capsize=3)
    for yi, n, m, h in zip(y, names, med, hi):
        s = q2["summary"][n]["burst"]
        note = f"  ({s['lost_total']:,} of {s['expected_per_rep'] * s['reps']:,} dropped by broker)" if s["lost_total"] else ""
        ax.text(m + h + max(med) * 0.02, yi, f"{m:,.0f}{note}", va="center", fontsize=8, color=INK2)
    ax.set_yticks(y, [Q2_LABELS.get(n, n) for n in names])
    ax.set_xlim(0, max(m + h for m, h in zip(med, hi)) * 1.6)
    ax.set_xlabel("events/s delivered to the consumer during a 3000-event burst (median, min–max)")
    ax.grid(axis="y", visible=False)
    ax.set_title("Q2  Saturation throughput of these implementations (N=10)", loc="left")
    fig.tight_layout()
    _save(fig, path)


def fig_q3a(a: dict, path: Path) -> None:
    names = list(a["configs"])
    med = [a["summary"][n]["lost"]["median"] for n in names]
    lo = [m - a["summary"][n]["lost"]["min"] for n, m in zip(names, med)]
    hi = [a["summary"][n]["lost"]["max"] - m for n, m in zip(names, med)]
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    y = list(range(len(names)))[::-1]
    ax.barh(y, med, height=0.5, color=S1)
    ax.errorbar(med, y, xerr=[lo, hi], fmt="none", ecolor=INK2, elinewidth=1, capsize=3)
    for yi, n, m, h in zip(y, names, med, hi):
        d = a["summary"][n]["duplicates_total"]
        ax.text(m + h + 4, yi, f"{m:.0f} lost" + (f", {d} dup" if d else ""), va="center", fontsize=8, color=INK2)
    ax.set_yticks(y, [Q3A_LABELS.get(n, n) for n in names])
    ax.set_xlim(0, max(max(m + h for m, h in zip(med, hi)), 1) * 1.3)
    ax.set_xlabel(f"events lost per run (median, min–max) — {a['rate']} ev/s, consumer down ≈2 s")
    ax.grid(axis="y", visible=False)
    ax.set_title("Q3a  Consumer outage: what the protocol does with events in between", loc="left")
    fig.tight_layout()
    _save(fig, path)


def fig_q3b(b: dict, path: Path) -> None:
    qs = b["queue_sizes"]
    labels = [f"queueSize={q}\n(revised {b['summary'][str(q)]['revised_queue_size'][0]})" for q in qs]
    med = [b["summary"][str(q)]["distinct"]["median"] for q in qs]
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    x = list(range(len(qs)))
    ax.bar(x, med, width=0.35, color=S1)
    for xi, m in zip(x, med):
        ax.text(xi, m + b["count"] * 0.02, f"{m:.0f} / {b['count']}", ha="center", fontsize=8, color=INK2)
    ax.set_xticks(x, labels)
    ax.set_ylim(0, b["count"] * 1.15)
    ax.set_ylabel("distinct values delivered")
    ax.grid(axis="x", visible=False)
    ax.set_title(f"Q3b  OPC UA: {b['count']} changes in ~1 s, publishing interval {b['publishing_interval_ms']:g} ms",
                 loc="left")
    fig.tight_layout()
    _save(fig, path)


def fig_q3c(c: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    probe = c.get("keepalive_probe", {})
    ks = sorted(int(k) for k in probe)
    if ks:
        ax.plot([0, max(ks) + 0.5], [0, 1.5 * (max(ks) + 0.5)], color=MUTED, linewidth=1, zorder=1)
        ax.text(max(ks) + 0.5, 1.5 * (max(ks) + 0.5), " 1.5 × K", color=MUTED, fontsize=8, va="center")
        xs, ys = [], []
        for k in ks:
            for v in probe[str(k)]["samples"]:
                xs.append(k)
                ys.append(v)
        ax.scatter(xs, ys, s=45, color=S1, edgecolors=SURFACE, linewidths=1.5, zorder=3,
                   label="silent raw client (time since CONNECT)")
    hx, hy = [], []
    for r in c["runs"]:
        if r["signal"] == "SIGSTOP" and r["ndeath_after_fail_ms"] is not None:
            hx.append(r["keepalive_s"] + 0.12)
            hy.append(r["ndeath_after_fail_ms"] / 1000)
    ax.scatter(hx, hy, s=45, color=S2, edgecolors=SURFACE, linewidths=1.5, zorder=3, marker="D",
               label="gateway SIGSTOP → NDEATH (time since stop)")
    crash = c["summary"].get("crash_k5")
    if crash:
        m = crash["ndeath_after_fail_ms"]["median"]
        ax.annotate(f"SIGKILL: NDEATH after {m:.1f} ms (socket closed by OS)", xy=(5, m / 1000), xytext=(5.3, 2.0),
                    fontsize=8, color=INK2, arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})
        ax.scatter([5], [m / 1000], s=45, color=S3, edgecolors=SURFACE, linewidths=1.5, zorder=3)
    ax.set_xlabel("MQTT keepalive K (s)")
    ax.set_ylabel("time until Will / NDEATH (s)")
    ax.set_xlim(0, (max(ks) if ks else 6) + 2)
    ax.set_ylim(0, max([*(hy or [0]), *([v for k in ks for v in probe[str(k)]["samples"]] or [0])]) * 1.15 + 1)
    ax.legend(loc="upper left")
    ax.set_title("Q3c  Failure detection by Mosquitto keepalive (Last Will = Sparkplug NDEATH)", loc="left")
    fig.tight_layout()
    _save(fig, path)


def write_report(results: dict, out: Path) -> None:
    out = Path(out)
    figs = out / "figures"
    figs.mkdir(exist_ok=True)
    parts = [meta_md(results)]
    if "q1" in results:
        parts.append(q1_md(results["q1"]))
        fig_q1(results["q1"], figs / "q1_bytes_per_event.png")
    if "q2" in results:
        parts.append(q2_md(results["q2"], results["reps"]))
        fig_q2_latency(results["q2"], figs / "q2_latency.png", results["reps"])
        fig_q2_throughput(results["q2"], figs / "q2_throughput.png")
    if "q3" in results:
        parts.append(q3_md(results["q3"], results["reps"]))
        fig_q3a(results["q3"]["a_outage"], figs / "q3a_outage_loss.png")
        fig_q3b(results["q3"]["b_coalescing"], figs / "q3b_opcua_coalescing.png")
        fig_q3c(results["q3"]["c_ndeath"], figs / "q3c_ndeath.png")
    (out / "results.md").write_text("\n\n".join(parts) + "\n")
