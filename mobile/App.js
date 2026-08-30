/* Plus100 mobile: quantitative decision engine (Expo Go, SDK 54).
   Data-dense dashboard: probability visuals, real formulas with this match's
   numbers, H2H / form / Elo-history analytics. Backend: FastAPI on Mac or
   cloud (server address editable in header). */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Feather, Ionicons } from "@expo/vector-icons";
import React, { useEffect, useRef, useState } from "react";
import {
  AccessibilityInfo, ActivityIndicator, Animated, Easing, Image, LayoutAnimation,
  Modal, Platform, Pressable, ScrollView, StatusBar, StyleSheet, Switch, Text,
  TextInput, UIManager, View,
} from "react-native";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";
import Svg, { Circle, Defs, G, Line, LinearGradient as SvgGrad, Path, Polyline, Rect,
  Stop, Text as SvgText } from "react-native-svg";

const DEFAULT_SERVER = "https://plus100.onrender.com";

/* Matchday, one standard mode: bright, airy, grass-green and alive — the feel of
   a summer World Cup afternoon, not a studio at midnight.
   Semantic slots (names stable across the codebase):
     lime  = primary accent: our model, actions, active states (grass green)
     sky   = the market / bookmakers / away side (kit blue)
     pitch = positive value, wins, confirmed edge (deep green)
     red   = losses, negative edge   amber = caution */
const C = {
  bg: "#F2F7F0", panel: "#FFFFFF", panel2: "#EAF2E6", line: "#D9E5D4",
  chalk: "#16251A", dim: "#49604C", muted: "#7C917E",
  lime: "#17A54B", limeDim: "rgba(23,165,75,0.12)", limeDeep: "#0C7A36",
  red: "#E23A50", amber: "#D97E06", sky: "#2D7FF0",
  pitch: "#0C8A3E", pitchDim: "rgba(12,138,62,0.10)", pitchDeep: "#0A5B33",
  onAccent: "#FFFFFF", statusBar: "dark-content",
};
// bright variants for text sitting on stadium photos and green gradients
const HERO = { home: "#5CE690", away: "#8FC1FF", draw: "#D8E4DA",
               txt: "#FFFFFF", dim: "rgba(255,255,255,0.78)", amber: "#FFD27A" };

const TNUM = { fontVariant: ["tabular-nums"] };

/* ---- kit colors: real team colors, adjusted so they stay readable ---- */
const hexRgb = (h) => {
  if (!h || typeof h !== "string") return null;
  const m = h.replace("#", "");
  if (m.length < 6) return null;
  const v = [0, 2, 4].map((i) => parseInt(m.slice(i, i + 2), 16));
  return v.some(Number.isNaN) ? null : v;
};
const rgbHex = (rgb) =>
  "#" + rgb.map((c) => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, "0")).join("");
const mixRgb = (rgb, t, to) => rgb.map((c, i) => c + (to[i] - c) * t);
const lumOf = (rgb) => (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;
function kitColor(hex, onDark) {
  const rgb = hexRgb(hex);
  if (!rgb) return null;
  const L = lumOf(rgb);
  if (onDark && L < 0.42) return rgbHex(mixRgb(rgb, 0.5 - L, [255, 255, 255]));
  if (!onDark && L > 0.68) return rgbHex(mixRgb(rgb, L - 0.45, [30, 40, 34]));
  return rgbHex(rgb);
}
const colorDist = (a, b) => {
  const x = hexRgb(a), y = hexRgb(b);
  return x && y ? Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]) : 999;
};
function matchColors(home, away, onDark) {
  const fbH = onDark ? HERO.home : C.lime;
  const fbA = onDark ? HERO.away : C.sky;
  const h = kitColor(home?.colors?.[0], onDark) || fbH;
  let a = kitColor(away?.colors?.[0], onDark);
  if (!a || colorDist(h, a) < 95) a = kitColor(away?.colors?.[1], onDark);
  if (!a || colorDist(h, a) < 95) a = colorDist(h, fbA) < 95 ? "#E8890C" : fbA;
  return [h, a];
}

/* rating pills: green = strong, amber = decent, grey = background */
const rateColor = (v, good, ok) => (v >= good ? "#0FA152" : v >= ok ? "#E8890C" : "#7C917E");

let REDUCE_MOTION = false;
try {
  const p = AccessibilityInfo?.isReduceMotionEnabled?.();
  if (p && typeof p.then === "function") {
    p.then((v) => { REDUCE_MOTION = !!v; }).catch(() => {});
  }
} catch { /* motion preference is optional; never block startup for it */ }
if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}
const springy = () => LayoutAnimation.configureNext(
  LayoutAnimation.create(REDUCE_MOTION ? 1 : 220, LayoutAnimation.Types.easeInEaseOut,
    LayoutAnimation.Properties.opacity));

let ODDS_FMT = "decimal";
function fmtOdds(dec) {
  if (dec == null) return "–";
  const d = Number(dec);
  if (!isFinite(d) || d <= 1) return "–";
  if (ODDS_FMT === "american") {
    return d >= 2 ? `+${Math.round((d - 1) * 100)}` : `${Math.round(-100 / (d - 1))}`;
  }
  return d.toFixed(2);
}

class ErrorBoundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <ScrollView style={{ flex: 1, backgroundColor: "#121212" }}
        contentContainerStyle={{ padding: 28, paddingTop: 80 }}>
        <Text style={{ color: "#EAB308", fontSize: 18, fontWeight: "700", marginBottom: 12 }}>
          Plus100 hit an error
        </Text>
        <Text style={{ color: "#F5F4F0", fontSize: 13, lineHeight: 20 }}>
          {String(this.state.err && this.state.err.message)}
        </Text>
        <Text style={{ color: "#7D7B73", fontSize: 11, marginTop: 16, lineHeight: 16 }}>
          {String(this.state.err && this.state.err.stack).slice(0, 900)}
        </Text>
      </ScrollView>
    );
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <SafeAreaProvider>
        <Root />
      </SafeAreaProvider>
    </ErrorBoundary>
  );
}

const SETTINGS_KEY = "plus100.settings.v1";

function Root() {
  const insets = useSafeAreaInsets();
  const [server, setServer] = useState(DEFAULT_SERVER);
  const [oddsFormat, setOddsFormat] = useState("decimal");
  const [bankroll, setBankroll] = useState("");
  const [fplId, setFplId] = useState("");
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState("predict");
  const [home, setHome] = useState(null);
  const [away, setAway] = useState(null);
  const [neutral, setNeutral] = useState(true);   // neutral venue by default
  const [prediction, setPrediction] = useState(null);
  const [h2h, setH2h] = useState(null);
  const [showDetails, setShowDetails] = useState(false);
  const [meta, setMeta] = useState(null);

  const [srv, setSrv] = useState("checking");   // checking | waking | warming | ok | down
  const probeRef = useRef(0);

  const fetchT = (url, ms) => {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), ms);
    return fetch(url, { signal: ctrl.signal }).finally(() => clearTimeout(t));
  };

  const api = async (path, params) => {
    const qs = params
      ? "?" + Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&")
      : "";
    let r;
    try {
      r = await fetchT(server.replace(/\/$/, "") + path + qs, 90000);
    } catch {
      throw new Error("couldn't reach the server");
    }
    if (r.status === 503) throw new Error("the model is still warming up, give it a minute");
    if (!r.ok) throw new Error(`server error ${r.status}`);
    return r.json();
  };

  // Health probe: wakes a sleeping cloud server, waits out model warmup, and if a
  // saved server address is dead (old Wi-Fi IP, expired tunnel) falls back to the
  // cloud default so the app never strands itself on a stale setting.
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    const id = ++probeRef.current;
    const base = server.replace(/\/$/, "");
    let fellBack = false;
    let failures = 0;
    const loop = async (attempt) => {
      if (!alive || probeRef.current !== id) return;
      try {
        const r = await fetchT(base + "/healthz", 25000);
        if (!r.ok) throw new Error("bad status");
        const h = await r.json();
        if (!alive || probeRef.current !== id) return;
        if (h.model_ready) {
          setSrv("ok");
          fetchT(base + "/api/meta", 30000).then((m) => m.json())
            .then((m) => { if (alive) setMeta(m); }).catch(() => {});
        } else {
          setSrv("warming");
          setTimeout(() => loop(attempt + 1), 8000);
        }
      } catch {
        if (!alive || probeRef.current !== id) return;
        failures += 1;
        if (server !== DEFAULT_SERVER && !fellBack && failures >= 5) {
          fellBack = true;
          // rewrite only the server field so other freshly-saved settings survive
          AsyncStorage.getItem(SETTINGS_KEY).then((raw) => {
            const sv = raw ? JSON.parse(raw) : {};
            sv.server = DEFAULT_SERVER;
            AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(sv)).catch(() => {});
          }).catch(() => {});
          setServer(DEFAULT_SERVER);
          return;
        }
        setSrv(attempt < 1 ? "checking" : attempt < 10 ? "waking" : "down");
        setTimeout(() => loop(attempt + 1), attempt < 10 ? 7000 : 20000);
      }
    };
    setSrv("checking");
    loop(0);
    return () => { alive = false; };
  }, [ready, server]);

  useEffect(() => {
    const failsafe = setTimeout(() => setReady(true), 2500);   // storage must never block the UI
    try {
      AsyncStorage.getItem(SETTINGS_KEY).then((raw) => {
      if (raw) {
        try {
          const sv = JSON.parse(raw);
          if (sv.server) setServer(sv.server);
          if (sv.oddsFormat) { setOddsFormat(sv.oddsFormat); ODDS_FMT = sv.oddsFormat; }
          if (sv.bankroll != null) setBankroll(String(sv.bankroll));
          if (sv.fplId != null) setFplId(String(sv.fplId));
        } catch { /* corrupt settings: fall back to defaults */ }
      }
        clearTimeout(failsafe);
        setReady(true);
      }).catch(() => { clearTimeout(failsafe); setReady(true); });
    } catch {
      clearTimeout(failsafe);
      setReady(true);
    }
    return () => clearTimeout(failsafe);
  }, []);

  const saveSettings = (patch) => {
    const merged = { server, oddsFormat, bankroll, fplId, ...patch };
    AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(merged)).catch(() => {});
    if (patch.server != null) setServer(patch.server);
    if (patch.oddsFormat != null) { setOddsFormat(patch.oddsFormat); ODDS_FMT = patch.oddsFormat; }
    if (patch.bankroll != null) setBankroll(patch.bankroll);
    if (patch.fplId != null) setFplId(patch.fplId);
  };

  const shared = {
    api, home, away, setHome, setAway, neutral, setNeutral,
    prediction, setPrediction, h2h, setH2h, meta,
    server, oddsFormat, bankroll, fplId, saveSettings,
    openDetails: () => setShowDetails(true),
  };

  const TABS = [
    ["predict", "crosshair", "Predict"],
    ["bets", "bar-chart-2", "Vs Market"],
    ["parlays", "layers", "Parlays"],
    ["fpl", "award", "Fantasy"],
    ["settings", "settings", "Settings"],
  ];

  if (!ready) return <View style={s.root} />;
  return (
    <View style={[s.root, { paddingTop: insets.top + 6, paddingBottom: insets.bottom }]}>
      <StatusBar barStyle={C.statusBar} backgroundColor={C.bg} />
      <View style={s.header}>
        <View style={s.row}>
          <View style={s.logoMark}><Text style={s.logoPlus}>+</Text></View>
          <Text style={[s.wordmark, { marginLeft: 10 }]}>Plus100</Text>
        </View>
      </View>

      {srv !== "ok" && (
        <View style={s.srvBanner}>
          {srv === "down"
            ? <Feather name="wifi-off" size={14} color={C.amber} />
            : <ActivityIndicator size="small" color={C.lime} />}
          <Text style={s.srvBannerTxt}>
            {srv === "checking" && "Connecting to the prediction server…"}
            {srv === "waking" && "Waking the cloud server. After a quiet spell this takes about a minute."}
            {srv === "warming" && "Server is up, the model is warming. Ready in about two minutes."}
            {srv === "down" && "Can't reach the server. Retrying, or check the address in Settings."}
          </Text>
        </View>
      )}

      <TabFade key={tab} style={{ flex: 1 }}>
        {tab === "predict" && <PredictScreen {...shared} />}
        {tab === "bets" && <BestBetsScreen {...shared} />}
        {tab === "parlays" && <ParlaysScreen {...shared} />}
        {tab === "fpl" && <FPLScreen {...shared} />}
        {tab === "settings" && <SettingsScreen {...shared} />}
      </TabFade>

      <View style={s.nav}>
        {TABS.map(([k, icon, label]) => (
          <Pressable key={k} style={[s.navBtn, tab === k && s.navBtnOn]} onPress={() => setTab(k)}
            accessibilityRole="tab" accessibilityLabel={label}>
            <Feather name={icon} size={19} color={tab === k ? C.lime : C.muted} />
            <Text style={[s.navTxt, tab === k && { color: C.lime, fontWeight: "700" }]}>{label}</Text>
          </Pressable>
        ))}
      </View>

      <DetailsModal visible={showDetails} onClose={() => setShowDetails(false)}
        prediction={prediction} meta={meta} />
    </View>
  );
}

/* ================= shared visual components ================= */

function TabFade({ children, style }) {
  const a = useRef(new Animated.Value(REDUCE_MOTION ? 1 : 0)).current;
  useEffect(() => {
    Animated.timing(a, { toValue: 1, duration: 240, easing: Easing.out(Easing.quad),
      useNativeDriver: true }).start();
  }, []);
  return (
    <Animated.View style={[style, { opacity: a,
      transform: [{ translateY: a.interpolate({ inputRange: [0, 1], outputRange: [10, 0] }) }] }]}>
      {children}</Animated.View>
  );
}

function GradientBG({ id, from, to, opacity = 1, style, rounded = 18 }) {
  return (
    <View pointerEvents="none"
      style={[{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0,
        borderRadius: rounded, overflow: "hidden" }, style]}>
      <Svg width="100%" height="100%">
        <Defs>
          <SvgGrad id={id} x1="0" y1="0" x2="1" y2="1">
            <Stop offset="0" stopColor={from} stopOpacity={opacity} />
            <Stop offset="1" stopColor={to} stopOpacity={opacity} />
          </SvgGrad>
        </Defs>
        <Rect x="0" y="0" width="100%" height="100%" fill={`url(#${id})`} />
      </Svg>
    </View>
  );
}

function CountUp({ value, decimals = 1, suffix = "%", style, duration = 900 }) {
  const [shown, setShown] = useState(REDUCE_MOTION ? value : 0);
  const a = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (REDUCE_MOTION) { setShown(value); return; }
    a.setValue(0);
    const id = a.addListener(({ value: t }) => setShown(t * value));
    Animated.timing(a, { toValue: 1, duration,
      easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
    return () => a.removeListener(id);
  }, [value]);
  return <Text style={[TNUM, style]}>{Number(shown).toFixed(decimals)}{suffix}</Text>;
}

function PressScale({ onPress, onLongPress, style, containerStyle, children, disabled,
  accessibilityLabel }) {
  const a = useRef(new Animated.Value(1)).current;
  const to = (v, fr, tn) => Animated.spring(a, { toValue: v, friction: fr, tension: tn,
    useNativeDriver: true }).start();
  return (
    <Pressable disabled={disabled} onPress={onPress} onLongPress={onLongPress}
      accessibilityLabel={accessibilityLabel} style={containerStyle}
      onPressIn={() => !REDUCE_MOTION && to(0.96, 6, 220)}
      onPressOut={() => to(1, 5, 180)}>
      <Animated.View style={[style, { transform: [{ scale: a }] }]}>
        {children}
      </Animated.View>
    </Pressable>
  );
}

function StackBar({ segs, height = 12 }) {
  const a = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    a.setValue(0);
    Animated.timing(a, { toValue: 1, duration: REDUCE_MOTION ? 1 : 750,
      easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
  }, [segs.map((x) => x.frac).join(",")]);
  return (
    <View style={{ height, borderRadius: height / 2, overflow: "hidden",
      flexDirection: "row", backgroundColor: C.panel2 }}>
      {segs.map((sg, i) => (
        <Animated.View key={i} style={{
          width: a.interpolate({ inputRange: [0, 1], outputRange: ["0%", `${sg.frac * 100}%`] }),
          backgroundColor: sg.color }} />
      ))}
    </View>
  );
}

function SectionTitle({ icon, children, note, accent }) {
  return (
    <View style={s.secTitleRow}>
      <Feather name={icon} size={accent ? 16 : 14} color={accent ? C.lime : C.muted} />
      <Text style={[s.h3, accent && { color: C.lime, fontSize: 16 }]}>  {children}</Text>
      {note ? <Text style={s.secNote}>{note}</Text> : null}
    </View>
  );
}

function Card({ children, style, delay = 0 }) {
  const a = useRef(new Animated.Value(REDUCE_MOTION ? 1 : 0)).current;
  useEffect(() => {
    Animated.timing(a, { toValue: 1, duration: 320, delay,
      easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  }, []);
  return (
    <Animated.View style={[s.card, style, {
      opacity: a,
      transform: [{ translateY: a.interpolate({ inputRange: [0, 1], outputRange: [16, 0] }) }],
    }]}>{children}</Animated.View>
  );
}

function FootballerArt({ size = 100, color = "#FFFFFF" }) {
  // sport-pictogram striker: mid-volley, ball leaving the boot
  return (
    <Svg width={size} height={size} viewBox="0 0 100 100">
      <G stroke={color} strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" fill="none">
        <Line x1="36" y1="24" x2="46" y2="46" />
        <Polyline points="36,26 20,38" />
        <Polyline points="36,26 52,14" />
        <Polyline points="46,46 40,64 28,76" />
        <Polyline points="46,46 62,40 76,30" />
      </G>
      <Circle cx="38" cy="12" r="8" fill={color} />
      <Circle cx="87" cy="23" r="9" fill={color} />
      <G stroke={color} strokeOpacity="0.5" strokeWidth="3" strokeLinecap="round">
        <Line x1="64" y1="10" x2="75" y2="10" />
        <Line x1="58" y1="2" x2="70" y2="2" />
      </G>
    </Svg>
  );
}

function BallDivider() {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 10,
      marginTop: 24, opacity: 0.65 }}>
      <View style={{ height: 1, backgroundColor: C.line, flex: 1 }} />
      <Ionicons name="football" size={14} color={C.muted} />
      <View style={{ height: 1, backgroundColor: C.line, flex: 1 }} />
    </View>
  );
}

function BallLoader({ label }) {
  const y = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (REDUCE_MOTION) return;
    Animated.loop(Animated.sequence([
      Animated.timing(y, { toValue: 1, duration: 430, easing: Easing.in(Easing.quad), useNativeDriver: true }),
      Animated.timing(y, { toValue: 0, duration: 430, easing: Easing.out(Easing.quad), useNativeDriver: true }),
    ])).start();
  }, []);
  return (
    <View style={{ alignItems: "center", marginTop: 26, marginBottom: 8 }}>
      <Animated.View style={{ transform: [
        { translateY: y.interpolate({ inputRange: [0, 1], outputRange: [-18, 0] }) },
        { rotate: y.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "140deg"] }) }] }}>
        <Ionicons name="football" size={36} color={C.chalk} />
      </Animated.View>
      <Animated.View style={{ width: 28, height: 5, borderRadius: 3, backgroundColor: "#1A2B1E",
        marginTop: 5,
        transform: [{ scaleX: y.interpolate({ inputRange: [0, 1], outputRange: [0.45, 1.15] }) }],
        opacity: y.interpolate({ inputRange: [0, 1], outputRange: [0.15, 0.4] }) }} />
      {label ? <Text style={[s.dimTxt, { marginTop: 14, textAlign: "center" }]}>{label}</Text> : null}
    </View>
  );
}

function TiltIn({ children, delay = 0 }) {
  const a = useRef(new Animated.Value(REDUCE_MOTION ? 1 : 0)).current;
  useEffect(() => {
    Animated.timing(a, { toValue: 1, duration: 680, delay,
      easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  }, []);
  return (
    <Animated.View style={{ opacity: a, transform: [{ perspective: 850 },
      { rotateX: a.interpolate({ inputRange: [0, 1], outputRange: ["24deg", "0deg"] }) },
      { translateY: a.interpolate({ inputRange: [0, 1], outputRange: [26, 0] }) }] }}>
      {children}
    </Animated.View>
  );
}

function StatTile({ label, value, sub, color }) {
  return (
    <View style={s.tile}>
      <Text style={[s.tileVal, TNUM, color ? { color } : null]} numberOfLines={1}
        adjustsFontSizeToFit>{value}</Text>
      <Text style={s.tileLabel}>{label}</Text>
      {sub ? <Text style={[s.tileSub, TNUM]}>{sub}</Text> : null}
    </View>
  );
}

function HBar({ frac, color, height = 7 }) {
  const [w, setW] = useState(0);
  const a = useRef(new Animated.Value(0)).current;
  const target = Math.min(Math.max(frac, 0), 1);
  useEffect(() => {
    if (w > 0) {
      Animated.timing(a, { toValue: target, duration: REDUCE_MOTION ? 1 : 620,
        easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
    }
  }, [w, target]);
  return (
    <View style={[s.barTrack, { height, borderRadius: height / 2 }]}
      onLayout={(e) => setW(e.nativeEvent.layout.width)}>
      <Animated.View style={{
        width: a.interpolate({ inputRange: [0, 1], outputRange: [0, w] }),
        height: "100%", backgroundColor: color, borderRadius: height / 2,
      }} />
    </View>
  );
}

function PairedBars({ market, model, blend }) {
  return (
    <View style={{ marginTop: 10 }}>
      {blend != null && (
        <Text style={[s.smallLabel, TNUM, { color: C.lime, textAlign: "right" }]}>
          {(blend * 100).toFixed(0)}% combined
        </Text>
      )}
      <View style={s.pairRow}>
        <Text style={s.pairTag}>books say</Text>
        <HBar frac={market} color={C.sky} />
        <Text style={[s.pairVal, TNUM, { color: C.sky }]}>{(market * 100).toFixed(0)}%</Text>
      </View>
      <View style={s.pairRow}>
        <Text style={s.pairTag}>we say</Text>
        <HBar frac={model} color={C.lime} />
        <Text style={[s.pairVal, TNUM, { color: C.lime }]}>{(model * 100).toFixed(0)}%</Text>
      </View>
    </View>
  );
}

function EMTrack({ pos, color }) {
  const [w, setW] = useState(0);
  const a = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (w > 0) {
      Animated.spring(a, { toValue: (pos / 100) * w, friction: 6, tension: 60,
        useNativeDriver: true }).start();
    }
  }, [w, pos]);
  return (
    <View style={s.emTrack} onLayout={(e) => setW(e.nativeEvent.layout.width)}>
      <View style={s.emZero} />
      <Animated.View style={[s.emDot, { backgroundColor: color,
        transform: [{ translateX: a }] }]} />
    </View>
  );
}

function EdgeMeter({ edgePct }) {
  const span = 15;
  const clamped = Math.max(-span, Math.min(span, edgePct));
  const pos = 50 + (clamped / span) * 50;
  const color = edgePct > 1 ? C.pitch : edgePct < -1 ? C.red : C.muted;
  return (
    <View style={{ marginTop: 10 }}>
      <Text style={[s.emVal, TNUM, { color }]}>{edgePct > 0 ? "+" : ""}{edgePct.toFixed(1)}%</Text>
      <EMTrack pos={pos} color={color} />
      <View style={s.rowBetween}>
        <Text style={s.emScale}>-{span}</Text>
        <Text style={s.emScale}>EDGE</Text>
        <Text style={s.emScale}>+{span}</Text>
      </View>
    </View>
  );
}

function Heatmap({ matrix, homeName, awayName }) {
  const N = 6;
  let maxP = 0;
  matrix.forEach((row) => row.forEach((v) => { maxP = Math.max(maxP, v); }));
  return (
    <View>
      <View style={s.heatRow}>
        <View style={s.heatHdr} />
        {[...Array(N)].map((_, j) => (
          <View key={j} style={s.heatHdr}><Text style={[s.heatHdrTxt, TNUM]}>{j}</Text></View>
        ))}
      </View>
      {matrix.slice(0, N).map((row, i) => (
        <View key={i} style={s.heatRow}>
          <View style={s.heatHdr}><Text style={[s.heatHdrTxt, TNUM]}>{i}</Text></View>
          {row.slice(0, N).map((v, j) => {
            const t = Math.pow(v / maxP, 0.7);
            return (
              <View key={j} style={[s.heatCell,
                { backgroundColor: `${C.lime}${Math.round(t * 0.82 * 255).toString(16).padStart(2, "0")}` }]}>
                <Text style={[s.heatTxt, TNUM, { color: t > 0.55 ? C.onAccent : C.dim }]}>
                  {(v * 100).toFixed(1)}
                </Text>
              </View>
            );
          })}
        </View>
      ))}
      <Text style={s.axisNote}>
        rows: {homeName} goals · columns: {awayName} goals · cell = % chance of that exact score
      </Text>
    </View>
  );
}

function EloLineChart({ histHome, histAway, nameHome, nameAway, w = 320, h = 130 }) {
  const all = [...histHome, ...histAway].map((p) => p[1]);
  if (!all.length) return null;
  const min = Math.min(...all) - 20;
  const max = Math.max(...all) + 20;
  const px = 34, py = 8;
  const pts = (hist) => hist.map((p, i) => {
    const x = px + (i / Math.max(hist.length - 1, 1)) * (w - px - 6);
    const y = py + (1 - (p[1] - min) / (max - min)) * (h - py * 2 - 14);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const firstDate = (histHome[0] || histAway[0] || [""])[0];
  const lastDate = (histHome[histHome.length - 1] || [""])[0];
  return (
    <View>
      <Svg width={w} height={h}>
        {[min + 20, (min + max) / 2, max - 20].map((v, i) => {
          const y = py + (1 - (v - min) / (max - min)) * (h - py * 2 - 14);
          return (
            <G key={i}>
              <Line x1={px} y1={y} x2={w - 6} y2={y} stroke={C.line} strokeWidth="0.5" />
              <SvgText x={2} y={y + 3} fill={C.muted} fontSize="8">{Math.round(v)}</SvgText>
            </G>
          );
        })}
        <Polyline points={pts(histHome)} fill="none" stroke={C.lime} strokeWidth="2" />
        <Polyline points={pts(histAway)} fill="none" stroke={C.sky} strokeWidth="2" />
      </Svg>
      <View style={s.rowBetween}>
        <Text style={s.axisNote}>{String(firstDate).slice(0, 7)}</Text>
        <View style={s.row}>
          <View style={[s.dot, { backgroundColor: C.lime }]} /><Text style={s.legendTxt}> {nameHome}   </Text>
          <View style={[s.dot, { backgroundColor: C.sky }]} /><Text style={s.legendTxt}> {nameAway}</Text>
        </View>
        <Text style={s.axisNote}>{String(lastDate).slice(0, 7)}</Text>
      </View>
    </View>
  );
}
function FormChips({ form }) {
  const col = { W: C.pitch, D: C.muted, L: C.red };
  return (
    <View style={s.rowWrap}>
      {form.map((f, i) => (
        <View key={i} style={[s.fchip, { backgroundColor: col[f.result] }]}
          accessibilityLabel={`${f.result} ${f.score} vs ${f.opponent}`}>
          <Text style={s.fchipTxt}>{f.result}</Text>
        </View>
      ))}
    </View>
  );
}

/* ================= matchup builder ================= */

function TeamSlot({ label, team, active, onPress }) {
  return (
    <PressScale onPress={onPress} containerStyle={{ flex: 1 }}
      style={[s.slotCard, active && s.slotCardOn]}
      accessibilityLabel={`${label} team`}>
      <View style={s.slotSideTag}>
        <Text style={s.slotSideTxt}>{label.toUpperCase()}</Text>
      </View>
      {team && team.badge
        ? <Image source={{ uri: team.badge }} style={s.slotBadge} />
        : <View style={s.slotBadgePh}>
            <Feather name={team ? "shield" : "plus"} size={20} color={active ? C.lime : C.muted} />
          </View>}
      <Text style={s.slotName} numberOfLines={1}>{team ? team.name : `Pick ${label.toLowerCase()}`}</Text>
      <Text style={[s.slotSub, TNUM]} numberOfLines={1}>
        {team
          ? (team.elo_delta
              ? `Elo ${team.elo} → ${team.elo_effective} (${(team.outs || []).length} out)`
              : `Elo ${Math.round(team.elo)}`)
          : "tap to search"}
      </Text>
    </PressScale>
  );
}

function TeamSearch({ api, onPick, placeholder }) {
  const [q, setQ] = useState("");
  const [opts, setOpts] = useState([]);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const search = (text) => {
    setQ(text);
    clearTimeout(timer.current);
    if (text.trim().length < 2) { setOpts([]); return; }
    timer.current = setTimeout(async () => {
      try { setOpts(await api("/api/teams", { q: text.trim() })); }
      catch { setOpts([]); }
    }, 220);
  };
  return (
    <View style={{ marginTop: 10 }}>
      <TextInput style={s.input} value={q} onChangeText={search} autoFocus
        placeholder={placeholder} placeholderTextColor={C.muted} autoCorrect={false} />
      {opts.length > 0 && (
        <View style={s.dropdown}>
          {opts.slice(0, 6).map((t) => (
            <Pressable key={t.id}
              style={({ pressed }) => [s.opt, pressed && { backgroundColor: C.panel2 }]}
              onPress={() => { setQ(""); setOpts([]); onPick(t); }}>
              <Text style={s.optName}>{t.name}</Text>
              <Text style={[s.optSub, TNUM]}>{t.league} · elo {Math.round(t.elo)}</Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}

/* ================= probable XI pitch ================= */

const ROW_Y = [0.92, 0.80, 0.685, 0.565];   // GK / DEF / MID / FWD, home half

function PitchSvg({ id, w, h }) {
  return (
    <Svg width={w} height={h}>
      <Defs>
        <SvgGrad id={id} x1="0" y1="0" x2="0" y2="1">
          <Stop offset="0" stopColor="#0F7A3C" />
          <Stop offset="0.5" stopColor="#0B5E2F" />
          <Stop offset="1" stopColor="#0F7A3C" />
        </SvgGrad>
      </Defs>
      <Rect width={w} height={h} fill={`url(#${id})`} />
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => i % 2 === 0 && (
        <Rect key={i} y={(h / 8) * i} width={w} height={h / 8}
          fill="#FFFFFF" opacity={0.045} />
      ))}
      <G stroke="#FFFFFF" strokeOpacity={0.35} strokeWidth={1.4} fill="none">
        <Rect x={8} y={8} width={w - 16} height={h - 16} rx={2} />
        <Line x1={8} y1={h / 2} x2={w - 8} y2={h / 2} />
        <Circle cx={w / 2} cy={h / 2} r={w * 0.13} />
        <Rect x={w * 0.2} y={8} width={w * 0.6} height={h * 0.115} />
        <Rect x={w * 0.2} y={h - 8 - h * 0.115} width={w * 0.6} height={h * 0.115} />
        <Rect x={w * 0.36} y={8} width={w * 0.28} height={h * 0.042} />
        <Rect x={w * 0.36} y={h - 8 - h * 0.042} width={w * 0.28} height={h * 0.042} />
      </G>
      <Circle cx={w / 2} cy={h / 2} r={2.2} fill="#FFFFFF" opacity={0.5} />
    </Svg>
  );
}

function PlayerDot({ p, color, delay, x, y, onPress, selected, badge }) {
  const a = useRef(new Animated.Value(REDUCE_MOTION ? 1 : 0)).current;
  // some photo CDNs refuse individual players; fall back to initials rather than
  // leaving an empty circle on the pitch
  const [imgOk, setImgOk] = useState(true);
  useEffect(() => { setImgOk(true); }, [p.img]);
  useEffect(() => {
    Animated.spring(a, { toValue: 1, friction: 6, tension: 130, delay,
      useNativeDriver: true }).start();
  }, []);
  const size = 44;
  if (p.placeholder) {
    return (
      <Animated.View style={{ position: "absolute", left: x - 31, top: y - size / 2 - 4,
        width: 62, alignItems: "center", opacity: 0.55, transform: [{ scale: a }] }}>
        <View style={[s.dotWrap, { borderColor: color, borderStyle: "dashed" }]}>
          <Text style={s.dotInitials}>?</Text>
        </View>
        <Text style={s.dotName} numberOfLines={1}>unknown</Text>
      </Animated.View>
    );
  }
  const last = p.name.split(" ").slice(-1)[0];
  const initials = p.name.split(" ").map((w) => w[0]).filter(Boolean).slice(0, 2).join("");
  return (
    <Animated.View style={{ position: "absolute", left: x - 31, top: y - size / 2 - 4,
      width: 62, alignItems: "center", transform: [{ scale: a }] }}>
      <Pressable onPress={onPress} accessibilityLabel={p.name} hitSlop={6}>
        <View style={[s.dotWrap, { borderColor: selected ? "#FFD24A" : color }]}>
          {p.img && imgOk
            ? <Image source={{ uri: p.img }} style={s.dotImg}
                onError={() => setImgOk(false)} />
            : <Text style={s.dotInitials}>{initials}</Text>}
        </View>
        {badge && (
          <View style={[s.dotBadge, { backgroundColor: badge.color }]}>
            <Text style={[s.dotBadgeTxt, TNUM]}>{badge.text}</Text>
          </View>
        )}
      </Pressable>
      <Text style={s.dotName} numberOfLines={1}>{last}</Text>
    </Animated.View>
  );
}

function PitchXI({ data, colors }) {
  const [w, setW] = useState(0);
  const [sel, setSel] = useState(null);
  // A handful of names is not a line-up; say so instead of drawing a broken pitch.
  if (data.home.known < 7 || data.away.known < 7) {
    const thin = [data.home, data.away].filter((t) => t.known < 7).map((t) => t.name);
    return (
      <Text style={s.dimTxt}>
        No usable squad list for {thin.join(" or ")} right now. The free squad feed only
        covers clubs in the current Premier League season plus whatever it holds for
        everyone else, so rather than invent a line-up we leave this out. The prediction
        above is unaffected.
      </Text>
    );
  }
  const h = Math.round(w * 1.62);
  const ring = colors || { home: "#FFFFFF", away: "#7CC4FF" };
  const place = (team, side) =>
    team.players.map((p, i) => {
      const fx = (p.slot + 1) / (p.n + 1);
      const x = (side === "home" ? fx : 1 - fx) * w;
      const y = (side === "home" ? ROW_Y[p.row] : 1 - ROW_Y[p.row]) * h;
      return (
        <PlayerDot key={side + p.name} p={p} x={x} y={y}
          color={ring[side]}
          badge={p.p_score != null ? { text: `${Math.round(p.p_score * 100)}%`,
            color: rateColor(p.p_score, 0.25, 0.12) } : null}
          selected={sel && sel.p.name === p.name && sel.side === side}
          delay={REDUCE_MOTION ? 0 : 120 + i * 45}
          onPress={() => { springy();
            setSel(sel && sel.p.name === p.name && sel.side === side ? null : { p, side }); }} />
      );
    });
  const Legend = ({ side }) => (
    <View style={s.row}>
      <View style={[s.dot, { backgroundColor: ring[side], borderWidth: 1,
        borderColor: C.line }]} />
      <View style={{ marginLeft: 7, flexShrink: 1 }}>
        <Text style={s.xiTeam} numberOfLines={1}>{data[side].name}</Text>
        <Text style={[s.optSub, TNUM, { marginTop: 1 }]}>
          {data[side].complete
            ? data[side].formation
            : `probable core · ${data[side].known} of 11 known`}
        </Text>
      </View>
    </View>
  );
  return (
    <View>
      <View style={s.rowBetween}>
        <Legend side="home" />
        <Legend side="away" />
      </View>
      <View style={{ marginTop: 14 }} onLayout={(e) => setW(e.nativeEvent.layout.width)}>
        {w > 0 && (
          <View style={{ width: w, height: h, borderRadius: 16, overflow: "hidden" }}>
            <PitchSvg id="turfxi" w={w} h={h} />
            {place(data.away, "away")}
            {place(data.home, "home")}
          </View>
        )}
      </View>
      {sel && (
        <View style={s.xiSelRow}>
          {sel.p.img
            ? <Image source={{ uri: sel.p.img }} style={s.xiSelImg} />
            : <View style={[s.xiSelImg, { backgroundColor: C.panel2 }]} />}
          <View style={{ flex: 1, marginLeft: 12 }}>
            <Text style={s.optName}>{sel.p.name}</Text>
            <Text style={s.optSub}>{sel.p.pos} · {data[sel.side].name}</Text>
            {sel.p.p_score != null && (
              <View style={[s.row, { marginTop: 6, gap: 8 }]}>
                <View style={{ flex: 1 }}><HBar frac={sel.p.p_score} color={C.pitch} height={6} /></View>
                <Text style={[s.optSub, TNUM, { color: C.pitch }]}>
                  scores {(sel.p.p_score * 100).toFixed(0)}% of the time
                </Text>
              </View>
            )}
          </View>
        </View>
      )}
      {((data.home.outs || []).length > 0 || (data.away.outs || []).length > 0) && (
        <View style={s.caveatRow}>
          <Feather name="user-x" size={13} color={C.amber} />
          <Text style={s.caveat}> Likely missing per team news: {[
            ...(data.home.outs || []).map((n) => `${n} (${data.home.name})`),
            ...(data.away.outs || []).map((n) => `${n} (${data.away.name})`),
          ].join(", ")}. They are left off the pitch and the prediction already
          accounts for them.</Text>
        </View>
      )}
      {[data.home, data.away].some((t) => !t.complete) && (
        <Text style={s.axisNote}>
          {[data.home, data.away].filter((t) => !t.complete).map((t) => t.name).join(" and ")}:
          the free squad feed only publishes part of the squad
          {[data.home, data.away].some((t) => !t.complete && t.gk_missing)
            ? ", with no goalkeeper listed" : ""}, so these are shown as the probable
          core rather than a made-up eleven.
        </Text>
      )}
      <Text style={s.axisNote}>{data.note} Tap any player for their role and scoring chance.</Text>
    </View>
  );
}

/* ================= goal-timing river ================= */

function GoalFlow({ lamH, lamA, colorH, colorA, nameH, nameA }) {
  const [w, setW] = useState(0);
  const h = 132, mid = h / 2, px = 6;
  // how goals really arrive: rising through each half, spiking in stoppage time
  const weight = (t) =>
    0.72 + 0.0072 * t
    + 0.55 * Math.exp(-((t - 45) ** 2) / 12)
    + 0.95 * Math.exp(-((t - 90) ** 2) / 16);
  const river = (lam, dir) => {
    const amp = mid - 12;
    const strength = Math.min(lam / 2.2, 1);
    let d = `M ${px} ${mid}`;
    for (let t = 0; t <= 90; t += 3) {
      const x = px + (t / 90) * (w - 2 * px);
      const y = mid - dir * (weight(t) / 2.62) * amp * strength;
      d += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
    }
    return d + ` L ${w - px} ${mid} Z`;
  };
  return (
    <View onLayout={(e) => setW(e.nativeEvent.layout.width)}>
      {w > 0 && (
        <Svg width={w} height={h}>
          <Path d={river(lamH, 1)} fill={colorH} opacity={0.8} />
          <Path d={river(lamA, -1)} fill={colorA} opacity={0.8} />
          <Line x1={w / 2} y1={10} x2={w / 2} y2={h - 10} stroke={C.muted}
            strokeWidth={1} strokeDasharray="3 4" opacity={0.7} />
          <Line x1={px} y1={mid} x2={w - px} y2={mid} stroke={C.panel} strokeWidth={1.4} />
        </Svg>
      )}
      <View style={s.rowBetween}>
        <Text style={s.axisNote}>kick-off</Text>
        <Text style={s.axisNote}>half-time</Text>
        <Text style={s.axisNote}>90'+</Text>
      </View>
      <View style={[s.row, { gap: 16, marginTop: 8 }]}>
        <View style={s.row}>
          <View style={[s.dot, { backgroundColor: colorH }]} />
          <Text style={s.legendTxt}> {nameH}</Text>
        </View>
        <View style={s.row}>
          <View style={[s.dot, { backgroundColor: colorA }]} />
          <Text style={s.legendTxt}> {nameA}</Text>
        </View>
      </View>
    </View>
  );
}

/* ================= live upcoming fixtures ================= */

function FixturesRow({ api, onLoad }) {
  const [fx, setFx] = useState(null);
  useEffect(() => {
    let alive = true;
    api("/api/fixtures/upcoming", { days: 8, limit: 15 })
      .then((d) => { if (alive) setFx(d.fixtures || []); })
      .catch(() => { if (alive) setFx([]); });
    return () => { alive = false; };
  }, []);
  if (!fx || fx.length === 0) return null;
  return (
    <View style={{ marginTop: 22 }}>
      <SectionTitle icon="calendar" note="real schedule, next 8 days">Kicking off soon</SectionTitle>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 10, paddingRight: 8 }}>
        {fx.map((f, i) => (
          <PressScale key={i} style={s.mqCard} onPress={() =>
            onLoad({ id: f.home_id, name: f.home, elo: f.home_elo, scope: "club", league: f.league },
                   { id: f.away_id, name: f.away, elo: f.away_elo, scope: "club", league: f.league })}>
            <Text style={s.mqTag} numberOfLines={1}>{f.league}</Text>
            <Text style={[s.optName, { marginTop: 6, fontSize: 13.5 }]} numberOfLines={1}>{f.home}</Text>
            <Text style={[s.optName, { fontSize: 13.5 }]} numberOfLines={1}>v {f.away}</Text>
            <Text style={[s.mqNames, TNUM]} numberOfLines={1}>
              {(() => {
                // offset-carrying kickoffs show the VIEWER's local time; bare
                // strings (older payloads) fall back to the raw UK clock time
                if (/[+-]\d\d:\d\d$|Z$/.test(f.kickoff)) {
                  const d = new Date(f.kickoff);
                  if (!isNaN(d)) {
                    return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")} · ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
                  }
                }
                return `${f.kickoff.slice(8, 10)}/${f.kickoff.slice(5, 7)} · ${f.kickoff.slice(11, 16)} UK`;
              })()}
            </Text>
          </PressScale>
        ))}
      </ScrollView>
    </View>
  );
}

/* ================= marquee matchups ================= */

const MARQUEE = [
  ["The final, replayed", "Spain", "Argentina"],
  ["The old rivalry", "Argentina", "England"],
  ["Samba vs Les Bleus", "Brazil", "France"],
  ["El Clasico", "Real Madrid", "Barcelona"],
  ["Premier League summit", "Liverpool", "Man City"],
  ["Kings of Europe", "Bayern Munich", "Paris SG"],
];

function MarqueeRow({ api, onLoad }) {
  const [cards, setCards] = useState(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      const found = await Promise.all(MARQUEE.map(async ([tag, hq, aq]) => {
        try {
          const [hs, as] = await Promise.all([
            api("/api/teams", { q: hq }), api("/api/teams", { q: aq })]);
          return hs[0] && as[0] ? { tag, h: hs[0], a: as[0] } : null;
        } catch { return null; }
      }));
      if (!alive) return;
      const out = found.filter(Boolean);
      setCards(out);
      out.forEach(async (c, i) => {
        try {
          const [hi, ai] = await Promise.all([
            api("/api/logo", { team_id: c.h.id }), api("/api/logo", { team_id: c.a.id })]);
          if (!alive) return;
          setCards((prev) => prev && prev.map((x, j) =>
            j === i ? { ...x, h: { ...x.h, ...hi }, a: { ...x.a, ...ai } } : x));
        } catch { /* badges stay as shields */ }
      });
    })();
    return () => { alive = false; };
  }, []);
  if (!cards) return null;
  const Badge = ({ t }) => t.badge
    ? <Image source={{ uri: t.badge }} style={s.mqBadge} />
    : <View style={[s.mqBadge, s.mqBadgePh]}><Feather name="shield" size={13} color={C.muted} /></View>;
  return (
    <View style={{ marginTop: 22 }}>
      <SectionTitle icon="star">Marquee matchups</SectionTitle>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 10, paddingRight: 8 }}>
        {cards.map((c, i) => (
          <PressScale key={i} style={s.mqCard} onPress={() => onLoad(c.h, c.a)}>
            <Text style={s.mqTag} numberOfLines={1}>{c.tag}</Text>
            <View style={[s.row, { marginTop: 9, gap: 7 }]}>
              <Badge t={c.h} />
              <Text style={s.mqVs}>v</Text>
              <Badge t={c.a} />
            </View>
            <Text style={s.mqNames} numberOfLines={1}>{c.h.name} · {c.a.name}</Text>
          </PressScale>
        ))}
      </ScrollView>
    </View>
  );
}

/* ================= predict screen ================= */
function PredictScreen(props) {
  const { api, home, away, setHome, setAway, neutral, setNeutral, meta,
          prediction, setPrediction, h2h, setH2h, openDetails } = props;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [activeSlot, setActiveSlot] = useState(null);   // "home" | "away" | null
  const [lineup, setLineup] = useState(null);
  const [lineupBusy, setLineupBusy] = useState(false);

  const autoNeutral = (a, b) =>
    setNeutral(!!(a && b && a.scope === "intl" && b.scope === "intl"));

  const loadPair = (ht, at) => {
    springy();
    setHome(ht); setAway(at);
    setNeutral(false);              // a listed fixture is a real home game
    setActiveSlot(null);
    for (const [t, set] of [[ht, setHome], [at, setAway]]) {
      api("/api/logo", { team_id: t.id })
        .then((info) => set((cur) => (cur && cur.id === t.id ? { ...cur, ...info } : cur)))
        .catch(() => {});
      api("/api/teamstate", { team_id: t.id })
        .then((st) => set((cur) => (cur && cur.id === t.id ? { ...cur, ...st } : cur)))
        .catch(() => {});
    }
  };

  const pickTeam = (slot, t) => {
    const set = slot === "home" ? setHome : setAway;
    set(t);
    autoNeutral(slot === "home" ? t : home, slot === "home" ? away : t);
    setActiveSlot(slot === "home" && !away ? "away" : null);
    springy();
    api("/api/logo", { team_id: t.id })
      .then((info) => set((cur) => (cur && cur.id === t.id ? { ...cur, ...info } : cur)))
      .catch(() => {});
    api("/api/teamstate", { team_id: t.id })
      .then((st) => set((cur) => (cur && cur.id === t.id ? { ...cur, ...st } : cur)))
      .catch(() => {});
  };

  const swapSides = () => {
    if (!home || !away) return;
    springy();
    const a = home, b = away;
    setHome(b); setAway(a);           // venue roles flip; the neutral toggle stays
    if (prediction) run(b, a);
  };

  const runId = useRef(0);
  const run = async (h = home, a = away, n = neutral) => {
    const id = ++runId.current;             // ignore late responses from older runs
    setBusy(true); setErr(""); setActiveSlot(null);
    setLineup(null); setLineupBusy(true);
    api("/api/lineup", { home: h.id, away: a.id, neutral: n })
      .then((lu) => { if (runId.current === id) setLineup(lu); })
      .catch(() => { if (runId.current === id) setLineup(null); })
      .finally(() => { if (runId.current === id) setLineupBusy(false); });
    try {
      const [p, hh] = await Promise.all([
        api("/api/predict", { home: h.id, away: a.id, neutral: n }),
        api("/api/h2h", { home: h.id, away: a.id }).catch(() => null),
      ]);
      if (runId.current !== id) return;
      setPrediction(p);
      setH2h(hh);
    } catch (e) { if (runId.current === id) setErr(String(e.message)); }
    if (runId.current === id) setBusy(false);
  };

  const p = prediction;
  const m = p?.markets?.one_x_two;
  const [kitH, kitA] = matchColors(home, away, true);     // dark surfaces: hero, turf
  const [kitHL, kitAL] = matchColors(home, away, false);  // white cards: goal chart
  const outcomes = p ? [
    { label: p.home.name, prob: m.home, color: C.lime, fair: m.fair_odds.home },
    { label: "Draw", prob: m.draw, color: C.muted, fair: m.fair_odds.draw },
    { label: p.away.name, prob: m.away, color: C.sky, fair: m.fair_odds.away },
  ] : [];
  const top = p ? [...outcomes].sort((a, b) => b.prob - a.prob)[0] : null;

  return (
    <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">
      {!p && !busy && (
        <View style={s.banner}>
          <GradientBG id="welcome" from="#1CB258" to="#0B7A38" rounded={20}
            style={{ left: -18, right: -18, top: -18, bottom: -18 }} />
          <View style={{ flex: 1, paddingRight: 10 }}>
            <Text style={s.bannerKicker}>SEASON 2026-27</Text>
            <Text style={s.bannerTitle}>Call any match on the planet</Text>
            <Text style={s.bannerSub}>
              <Text style={[TNUM, { fontWeight: "800", color: "#FFFFFF" }]}>
                {meta ? meta.matches.toLocaleString() : "…"}
              </Text> matches · {meta ? meta.teams : "…"} teams · {meta ? meta.leagues : "…"} leagues
            </Text>
          </View>
          <View style={{ zIndex: 2 }}><FootballerArt size={92} /></View>
        </View>
      )}

      {/* -------- matchup builder -------- */}
      <View style={s.slotRow}>
        <TeamSlot label="Home" team={home} active={activeSlot === "home"}
          onPress={() => { springy(); setActiveSlot(activeSlot === "home" ? null : "home"); }} />
        <View style={s.vsWrap}>
          <Text style={s.vsTxt}>VS</Text>
          <Pressable disabled={!home || !away} onPress={swapSides} hitSlop={10}
            accessibilityLabel="Switch home and away"
            style={[s.swapBtn, (!home || !away) && { opacity: 0.35 }]}>
            <Feather name="repeat" size={15} color={C.dim} />
          </Pressable>
        </View>
        <TeamSlot label="Away" team={away} active={activeSlot === "away"}
          onPress={() => { springy(); setActiveSlot(activeSlot === "away" ? null : "away"); }} />
      </View>
      {activeSlot && (
        <TeamSearch api={api} placeholder={`Search the ${activeSlot} team…`}
          onPick={(t) => pickTeam(activeSlot, t)} />
      )}

      <PressScale disabled={!home || !away || busy} onPress={() => run()}
        style={[s.cta, (!home || !away || busy) && s.btnOff]}>
        <GradientBG id="cta" from="#1FB25A" to="#0B7A38" rounded={16} />
        <Feather name="zap" size={17} color={C.onAccent} />
        <Text style={s.ctaTxt}>{busy ? "Computing…" : "Predict this match"}</Text>
      </PressScale>

      <View style={[s.row, { marginTop: 14, justifyContent: "center" }]}>
        <Switch value={neutral} onValueChange={setNeutral}
          trackColor={{ true: C.lime, false: C.line }} thumbColor="#FFFFFF" />
        <Text style={s.dimTxt}>  neutral venue</Text>
      </View>

      {!!err && <Text style={s.errTxt}>{err}. If this keeps happening, check the server address in Settings.</Text>}
      {busy && <BallLoader label="Kicking off: replaying the full match history…" />}
      {!p && !busy && (
        <>
          <FixturesRow api={api} onLoad={loadPair} />
          <MarqueeRow api={api}
            onLoad={(h, a) => { springy(); setHome(h); setAway(a);
              autoNeutral(h, a); setActiveSlot(null); }} />
          <BallDivider />
          <Text style={[s.axisNote, { textAlign: "center", marginTop: 10 }]}>
            Pick two teams, or tap a marquee matchup to load one instantly.
          </Text>
        </>
      )}

      {p && !busy && (
        <>
          {/* -------- hero: outcome probabilities over the likelier winner -------- */}
          <Card delay={0} style={s.heroCard}>
            {(() => {
              const likelier = m.home >= m.away ? home : away;
              const art = likelier?.fanart || home?.fanart || away?.fanart;
              return art ? (
                <>
                  <Image source={{ uri: art }} style={s.heroPhoto} blurRadius={1} />
                  <View style={s.heroShade} />
                </>
              ) : <GradientBG id="hero" from="#188F46" to="#0A6B31" rounded={18}
                    style={{ left: -20, right: -20, top: -20, bottom: -20 }} />;
            })()}
            <View style={s.heroRow}>
              <View style={s.heroCol}>
                {home?.badge ? <Image source={{ uri: home.badge }} style={s.heroBadge} />
                  : <View style={[s.heroBadge, s.heroBadgePh]}><Feather name="shield" size={22} color={HERO.dim} /></View>}
                <Text style={s.heroName} numberOfLines={1}>{p.home.name}</Text>
                <View style={s.heroSideTag}><Text style={s.heroSideTxt}>{neutral ? "NEUTRAL" : "HOME"}</Text></View>
                <CountUp value={m.home * 100} style={[s.heroPct, { color: kitH }]} />
                <Text style={[s.heroFair, TNUM]}>fair {fmtOdds(m.fair_odds.home)}</Text>
              </View>
              <View style={[s.heroCol, { flex: 0.7 }]}>
                <Text style={s.heroDrawLbl}>DRAW</Text>
                <CountUp value={m.draw * 100} style={s.heroDraw} />
                <Text style={[s.heroFair, TNUM]}>fair {fmtOdds(m.fair_odds.draw)}</Text>
              </View>
              <View style={s.heroCol}>
                {away?.badge ? <Image source={{ uri: away.badge }} style={s.heroBadge} />
                  : <View style={[s.heroBadge, s.heroBadgePh]}><Feather name="shield" size={22} color={HERO.dim} /></View>}
                <Text style={s.heroName} numberOfLines={1}>{p.away.name}</Text>
                <View style={s.heroSideTag}><Text style={s.heroSideTxt}>{neutral ? "NEUTRAL" : "AWAY"}</Text></View>
                <CountUp value={m.away * 100} style={[s.heroPct, { color: kitA }]} />
                <Text style={[s.heroFair, TNUM]}>fair {fmtOdds(m.fair_odds.away)}</Text>
              </View>
            </View>
            <View style={{ marginTop: 14 }}>
              <StackBar segs={[{ frac: m.home, color: kitH },
                { frac: m.draw, color: (lumOf(hexRgb(kitH) || [0,0,0]) > 0.75 ||
                    lumOf(hexRgb(kitA) || [0,0,0]) > 0.75)
                    ? "rgba(30,42,34,0.55)" : "rgba(255,255,255,0.35)" },
                { frac: m.away, color: kitA }]} />
            </View>
            {!!home?.stadium && !neutral && (
              <View style={[s.row, { justifyContent: "center", marginTop: 12, gap: 5 }]}>
                <Feather name="map-pin" size={12} color={HERO.dim} />
                <Text style={[s.heroFair, TNUM]}>
                  {home.stadium}{home.capacity ? ` · ${Number(home.capacity).toLocaleString()} seats` : ""}
                </Text>
              </View>
            )}
            <View style={s.tileRow}>
              {[
                [`${Number(p.expected_goals.home).toFixed(1)}–${Number(p.expected_goals.away).toFixed(1)}`, "expected goals", HERO.txt],
                ...(() => { const eff = p.home.elo_effective != null && p.away.elo_effective != null;
                  const d = eff ? p.home.elo_effective - p.away.elo_effective : p.model_detail.elo_diff;
                  return [[`${d > 0 ? "+" : ""}${Math.round(d)}`,
                           eff ? "elo edge, today's squads" : "elo edge", HERO.home]]; })(),
                [`${(100 - top.prob * 100).toFixed(0)}%`, "misses anyway", HERO.amber],
              ].map(([v, lbl, col]) => (
                <View key={lbl} style={s.heroTile}>
                  <Text style={[s.heroTileVal, TNUM, { color: col }]} numberOfLines={1}>{v}</Text>
                  <Text style={s.heroTileLbl}>{lbl}</Text>
                </View>
              ))}
            </View>
            <Pressable style={({ pressed }) => [s.heroDetailsBtn, pressed && { opacity: 0.8 }]}
              onPress={openDetails}>
              <Feather name="divide-circle" size={14} color={HERO.txt} />
              <Text style={s.heroDetailsTxt}>  The math, with this match's numbers</Text>
            </Pressable>
          </Card>

          {/* -------- when the goals should come -------- */}
          <Card delay={30}>
            <SectionTitle icon="clock" note="stoppage-time spikes are real">
              When the goals should come</SectionTitle>
            <GoalFlow lamH={p.expected_goals.home} lamA={p.expected_goals.away}
              colorH={kitHL} colorA={kitAL} nameH={p.home.name} nameA={p.away.name} />
            <Text style={s.axisNote}>
              Each side's scoring threat minute by minute, in their real colors: the model's
              expected goals spread the way goals actually arrive, climbing through each half
              and spiking in stoppage time. The wider the river, the likelier they strike.
            </Text>
          </Card>

          {/* -------- probable line-ups on the pitch -------- */}
          <Card delay={60}>
            <SectionTitle icon="users">Probable line-ups</SectionTitle>
            {lineup ? <TiltIn><PitchXI data={lineup} colors={{ home: kitH, away: kitA }} /></TiltIn>
              : lineupBusy ? <BallLoader label="Warming up: pulling squads and player photos…" />
              : <Text style={s.dimTxt}>No public squad data for this pairing yet.</Text>}
          </Card>

          {/* -------- scoreline heatmap -------- */}
          <Card delay={70}>
            <SectionTitle icon="grid" note={`most likely is ~1 in ${Math.max(2, Math.round(1 / p.markets.correct_scores[0].prob))}`}>Exact score probabilities</SectionTitle>
            <Heatmap matrix={p.score_matrix} homeName={p.home.name} awayName={p.away.name} />
            <View style={[s.rowWrap, { marginTop: 10 }]}>
              {p.markets.correct_scores.slice(0, 4).map((cs) => (
                <View key={cs.score} style={s.chip}>
                  <Text style={[s.chipTop, TNUM]}>{cs.score}</Text>
                  <Text style={[s.chipSub, TNUM]}>{(cs.prob * 100).toFixed(1)}%</Text>
                </View>
              ))}
            </View>
          </Card>

          {/* -------- markets -------- */}
          <Card delay={140}>
            <SectionTitle icon="list">Every market, our fair price</SectionTitle>
            {[
              ["Over 2.5 goals", p.markets.totals["2.5"].over, 1 / p.markets.totals["2.5"].over],
              ["Under 2.5 goals", p.markets.totals["2.5"].under, 1 / p.markets.totals["2.5"].under],
              ["Both teams score", p.markets.btts.yes, 1 / p.markets.btts.yes],
              [`${p.home.name} or draw`, p.markets.double_chance["1X"], 1 / p.markets.double_chance["1X"]],
              [`${p.home.name} clean sheet`, p.markets.clean_sheet.home, 1 / p.markets.clean_sheet.home],
              [`${p.away.name} clean sheet`, p.markets.clean_sheet.away, 1 / p.markets.clean_sheet.away],
            ].map(([label, prob, fair]) => (
              <View key={label} style={s.mktRow}>
                <Text style={s.mktLabel} numberOfLines={1}>{label}</Text>
                <View style={{ flex: 1, marginHorizontal: 10 }}>
                  <HBar frac={prob} color={C.sky} height={5} />
                </View>
                <Text style={[s.mktVal, TNUM]}>{(prob * 100).toFixed(0)}%</Text>
                <Text style={[s.mktFair, TNUM]}>{fmtOdds(fair)}</Text>
              </View>
            ))}
            <Text style={s.axisNote}>bet any of these only when a book offers MORE than the fair price</Text>
          </Card>

          {/* -------- scorers -------- */}
          {Object.entries(p.likely_scorers || {}).map(([team, players]) => players.length > 0 && (
            <Card key={team}>
              <SectionTitle icon="target">Chance to score · {team}</SectionTitle>
              {players.slice(0, 5).map((x) => (
                <View key={x.player} style={s.scorerRow}>
                  <Text style={s.scorerName} numberOfLines={1}>{x.player}</Text>
                  <View style={{ flex: 1 }}><HBar frac={x.prob_to_score} color={C.lime} /></View>
                  <Text style={[s.scorerP, TNUM,
                    { color: rateColor(x.prob_to_score, 0.25, 0.12), fontWeight: "700" }]}>
                    {(x.prob_to_score * 100).toFixed(0)}%</Text>
                </View>
              ))}
            </Card>
          ))}

          {/* -------- head to head + form -------- */}
          {h2h && (
            <Card>
              <SectionTitle icon="repeat">Head to head</SectionTitle>
              {h2h.summary.played > 0 ? (
                <>
                  <View style={s.tileRow}>
                    <StatTile label={h2h.teams.home.name} color={C.lime} value={h2h.summary.wins_home} sub="wins" />
                    <StatTile label="draws" value={h2h.summary.draws} sub={`${h2h.summary.played} met`} />
                    <StatTile label={h2h.teams.away.name} color={C.sky} value={h2h.summary.wins_away} sub="wins" />
                  </View>
                  {h2h.meetings.slice(0, 5).map((mt, i) => (
                    <View key={i} style={s.meetRow}>
                      <Text style={[s.meetDate, TNUM]}>{mt.date.slice(0, 7)}</Text>
                      <Text style={s.meetMatch} numberOfLines={1}>{mt.home} v {mt.away}</Text>
                      <Text style={[s.meetScore, TNUM]}>{mt.score}</Text>
                    </View>
                  ))}
                </>
              ) : <Text style={s.dimTxt}>These teams have never met in our data.</Text>}

              <SectionTitle icon="activity" note="newest first">Recent form</SectionTitle>
              <Text style={s.smallLabel}>{h2h.teams.home.name}</Text>
              <FormChips form={h2h.form.home} />
              <Text style={[s.smallLabel, { marginTop: 8 }]}>{h2h.teams.away.name}</Text>
              <FormChips form={h2h.form.away} />

              <SectionTitle icon="trending-up">Strength over time (Elo)</SectionTitle>
              <EloLineChart histHome={h2h.elo_history.home} histAway={h2h.elo_history.away}
                nameHome={h2h.teams.home.name} nameAway={h2h.teams.away.name} />
            </Card>
          )}

          {(p.caveats || []).map((c, i) => (
            <View key={i} style={s.caveatRow}>
              <Feather name="alert-triangle" size={13} color={C.amber} />
              <Text style={s.caveat}> {c}</Text>
            </View>
          ))}

          {/* the schedule stays reachable after a prediction: tap to jump matches */}
          <FixturesRow api={api}
            onLoad={(ht, at) => { loadPair(ht, at); run(ht, at, false); }} />
        </>
      )}
    </ScrollView>
  );
}

/* ================= vs market ================= */
function BestBetsScreen({ api, home, away, bankroll }) {
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  const run = async () => {
    setBusy(true); setErr(""); setData(null);
    try {
      const params = home && away ? { home: home.id, away: away.id } : undefined;
      setData(await api("/api/bestbets", params));
    } catch (e) { setErr(String(e.message)); }
    setBusy(false);
  };

  const rows = data?.selected?.bets || data?.bets || [];
  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>Bookmakers vs our model</Text>
      <Text style={s.dimTxt}>
        This page answers one question: is any sportsbook price actually worth taking?
        It pulls live odds from about 15 US sportsbooks, strips out their built-in margin to
        reveal the probability THEY believe, and puts it next to OURS. For every bet you get
        two bars: blue is the bookmakers' probability, green is our model's. When the best
        available price pays more than the combined estimate justifies, the card lights up
        green with a stake suggestion. No green card means no good bet exists, and skipping
        is the right move.
      </Text>
      <Pressable style={({ pressed }) => [s.btn, busy && s.btnOff,
        { marginTop: 14, alignItems: "center" }, pressed && { opacity: 0.8 }]}
        disabled={busy} onPress={run}>
        <Text style={s.btnTxt}>{busy ? "Checking prices…" : home && away ? "Compare this match" : "Scan the next two days"}</Text>
      </Pressable>
      {!!err && <Text style={s.errTxt}>{err}</Text>}
      {busy && <BallLoader label="Shopping 15 sportsbooks for prices…" />}

      {data && !busy && data.error && (
        <Card>
          <SectionTitle icon="alert-circle">The odds feed isn't available</SectionTitle>
          <Text style={s.dimTxt}>
            {data.error === "quota"
              ? "The odds allowance for this month is used up; it resets on the 1st."
              : "The server has no working Odds API key configured, so live prices can't be fetched."}
          </Text>
        </Card>
      )}
      {data && !busy && !data.error && (
        <>
          {data.selected && <Text style={s.h3}>{data.selected.match}</Text>}
          {rows.length === 0 && (
            <Card>
              <SectionTitle icon="info">Nothing to grade right now</SectionTitle>
              <Text style={s.dimTxt}>
                {home && !data.selected
                  ? (data.fixtures === 1
                      ? "The books list this match, but none of its markets can be graded right now."
                      : "The sportsbooks aren't listing this match yet. Books usually price games one or two days before kickoff, so check back then.")
                  : data.fixtures === 0
                    ? "The sportsbooks aren't listing any football in the next two days. This happens between rounds and in the offseason. Check back on a match week."
                    : `Checked ${data.fixtures} listed game${data.fixtures === 1 ? "" : "s"}. No price beats the combined estimate right now, which is the normal state. Not betting today costs you nothing.`}
              </Text>
              {data.skipped?.length > 0 && (
                <Text style={[s.axisNote, { marginTop: 8 }]}>
                  skipped: {data.skipped.slice(0, 3).join(", ")}
                </Text>
              )}
            </Card>
          )}
          {rows.map((b, i) => {
            const good = b.edge_pct > 1;
            return (
              <Card key={i} style={good ? s.cardGood : null}>
                <View style={s.rowBetween}>
                  <Text style={s.betOutcome}>{b.outcome}</Text>
                  {good ? (
                    <View style={s.edgeChip}>
                      <Feather name="trending-up" size={12} color={C.onAccent} />
                      <Text style={[s.edgeChipTxt, TNUM]}> +{b.edge_pct}%</Text>
                    </View>
                  ) : <Text style={s.noValTxt}>no value</Text>}
                </View>
                {!data.selected && <Text style={s.dimTxt}>{b.match}</Text>}
                <PairedBars market={b.p_market} model={b.p_model} blend={b.p_blend} />
                <EdgeMeter edgePct={b.edge_pct} />
                <Text style={[s.betLine, TNUM]}>
                  best price {fmtOdds(b.odds)} at {b.book} · implies {(100 / b.odds).toFixed(0)}%
                </Text>
                {good && (
                  <Text style={[s.betLine, TNUM, { color: C.pitch }]}>
                    {Number(bankroll) > 0
                      ? `stake about $${(Number(bankroll) * b.quarter_kelly_pct / 100).toFixed(0)} (${b.quarter_kelly_pct}% of your $${Number(bankroll).toLocaleString()} bankroll)`
                      : `stake ${b.quarter_kelly_pct}% of bankroll (set yours in Settings for dollar amounts)`}
                  </Text>
                )}
              </Card>
            );
          })}
          {data.remaining_credits != null && (
            <Text style={[s.axisNote, { marginTop: 10 }]}>
              odds credits left: {data.remaining_credits} · edges are long-run advantages, not sure things
            </Text>
          )}
        </>
      )}
    </ScrollView>
  );
}

/* ================= parlays ================= */
function ParlaysScreen({ api, home, away, neutral }) {
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!home || !away) { setList(null); return; }
    let alive = true;
    setErr(""); setList(null);
    api("/api/parlay/suggest", { home: home.id, away: away.id, neutral })
      .then((d) => { if (alive) setList(d); })
      .catch((e) => { if (alive) setErr(String(e.message)); });
    return () => { alive = false; };
  }, [home?.id, away?.id, neutral]);

  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>Parlay builds</Text>
      {!home || !away ? (
        <Text style={s.dimTxt}>Pick a match on the Predict tab first.</Text>
      ) : err ? (
        <Text style={s.errTxt}>{err}</Text>
      ) : !list ? (
        <BallLoader label="Simulating this match 150,000 times…" />
      ) : (
        <>
          <Text style={s.dimTxt}>
            True hit chance from 150,000 simulated matches. Build the combo in your sportsbook
            and bet ONLY if their payout beats the "take if" number. Apply any 4+ leg boost first.
          </Text>
          {list.length === 0 && (
            <Card>
              <Text style={s.dimTxt}>
                No parlay clears the bar for this match: every template combo lands under
                a 1.5% real chance, which is lottery territory. Skipping is the play.
              </Text>
            </Card>
          )}
          {list.map((pl) => (
            <Card key={pl.name}>
              <Text style={s.suggName}>{pl.name}
                {pl.n_legs >= 4 ? "  ·  Boost eligible" : ""}</Text>
              <Text style={s.betOutcome}>{pl.labels.join("  +  ")}</Text>
              <View style={{ marginTop: 10 }}><HBar frac={pl.joint_prob} color={C.lime} /></View>
              <View style={s.tileRow}>
                <StatTile label="hits" value={`${(pl.joint_prob * 100).toFixed(1)}%`} color={C.lime} />
                <StatTile label="fair odds" value={fmtOdds(pl.fair_odds)} />
                <StatTile label="take if ≥" value={fmtOdds(pl.min_quote)} color={C.amber} />
              </View>
              {pl.correlation_boost > 1.15 && (
                <Text style={[s.axisNote, { color: C.sky }]}>
                  legs reinforce each other ×{pl.correlation_boost} vs independent, and books often underpay these
                </Text>
              )}
            </Card>
          ))}
        </>
      )}
    </ScrollView>
  );
}

/* ================= fantasy: editable best XI ================= */

const XI_ROW = { GK: 0.90, DEF: 0.70, MID: 0.47, FWD: 0.22 };

/* ================= the persistent model team ================= */

function ModelTeam({ api }) {
  const [t, setT] = useState(null);
  const [err, setErr] = useState("");
  const [w, setW] = useState(0);
  useEffect(() => {
    api("/api/fpl/squad").then((d) => {
      if (d.error) setErr(d.detail || d.error); else setT(d);
    }).catch((e) => setErr(String(e.message)));
  }, []);
  if (err) return <Text style={s.dimTxt}>{err}</Text>;
  if (!t) return <BallLoader label="Fetching the team and its live score…" />;
  const byId = Object.fromEntries(t.squad.map((p) => [p.id, p]));
  const xi = t.xi.map((id) => byId[id]).filter(Boolean);
  const bench = t.squad.filter((p) => !t.xi.includes(p.id));
  const capName = byId[t.captain] ? byId[t.captain].name : "";
  const rows = { GK: [], DEF: [], MID: [], FWD: [] };
  xi.forEach((p) => rows[p.pos].push(p));
  const h = Math.round(w * 1.15);
  const move = t.this_week.length
    ? t.this_week.map((x) => `${x.out} out, ${x.in} in (+${x.gain})`).join("; ")
    : "held — nothing cleared the bar, the free transfer banks";
  return (
    <View>
      <View style={s.tileRow}>
        <StatTile label="actual points" value={t.season_points} color={C.pitch} />
        <StatTile label="live this round" value={t.live_gw_points} color={C.lime} />
        <StatTile label="projected next" value={t.projected_points} />
      </View>
      <View style={[s.rowBetween, { marginTop: 10 }]}>
        <Text style={[s.smallLabel, TNUM]}>£{t.bank.toFixed(1)}m banked · {t.banked_transfers} free transfer{t.banked_transfers === 1 ? "" : "s"}</Text>
        <Text style={[s.smallLabel, { color: C.lime, fontWeight: "700" }]}>C: {capName}</Text>
      </View>
      <Text style={[s.dimTxt, { marginTop: 8 }]}>This week: {move}.</Text>
      <View style={{ marginTop: 12 }} onLayout={(e) => setW(e.nativeEvent.layout.width)}>
        {w > 0 && (
          <View style={{ width: w, height: h, borderRadius: 16, overflow: "hidden" }}>
            <PitchSvg id="turfmodel" w={w} h={h} />
            {Object.entries(rows).flatMap(([pos, list]) =>
              list.map((p, i) => (
                <PlayerDot key={p.id}
                  x={((i + 1) / (list.length + 1)) * w} y={XI_ROW[pos] * h}
                  p={{ ...p, img: p.photo }}
                  badge={{ text: p.xpts.toFixed(1), color: rateColor(p.xpts, 5, 3) }}
                  color={p.id === t.captain ? "#FFD24A" : "#FFFFFF"}
                  delay={REDUCE_MOTION ? 0 : 100 + i * 40} />
              )))}
          </View>
        )}
      </View>
      <View style={s.benchRow}>
        <Text style={[s.xiScoreLbl, { marginRight: 10 }]}>bench</Text>
        {bench.map((p) => (
          <View key={p.id} style={{ alignItems: "center", width: 56 }}>
            {p.photo ? <Image source={{ uri: p.photo }} style={s.benchImg} />
              : <View style={[s.benchImg, { backgroundColor: C.panel2 }]} />}
            <Text style={s.benchName} numberOfLines={1}>{p.name}</Text>
            <Text style={[s.benchName, TNUM, { color: C.muted }]}>£{p.price.toFixed(1)}m</Text>
          </View>
        ))}
      </View>
      {t.scores.length > 0 && (
        <Text style={[s.axisNote, { marginTop: 8 }]}>
          Finished rounds: {t.scores.map((x) => `GW${x.gw}: ${x.points}`).join(" · ")}
        </Text>
      )}
      <Text style={s.axisNote}>{t.note}</Text>
    </View>
  );
}

/* ================= fantasy premier league ================= */
function FPLScreen({ api, fplId }) {
  const [gw, setGw] = useState(null);
  const [err, setErr] = useState("");
  const [pos, setPos] = useState("MID");
  const [expanded, setExpanded] = useState(null);
  const [team, setTeam] = useState(null);
  const [teamErr, setTeamErr] = useState("");

  useEffect(() => {
    api("/api/fpl/gw").then((d) => {
      if (d.error) setErr(d.detail || d.error); else setGw(d);
    }).catch((e) => setErr(String(e.message)));
  }, []);

  useEffect(() => {
    if (!fplId || !gw) return;
    let alive = true;
    setTeam(null); setTeamErr("");
    api(`/api/fpl/entry/${fplId}`).then((d) => {
      if (!alive) return;
      if (d.error) setTeamErr(d.detail || d.error); else setTeam(d);
    }).catch((e) => { if (alive) setTeamErr(String(e.message)); });
    return () => { alive = false; };
  }, [fplId, gw?.gameweek]);

  if (err) return <ScrollView contentContainerStyle={s.scroll}><Text style={s.errTxt}>{err}</Text></ScrollView>;
  if (!gw) return <ScrollView contentContainerStyle={s.scroll}><BallLoader label="Loading this gameweek…" /></ScrollView>;

  const days = Math.max(0, Math.ceil((new Date(gw.deadline) - Date.now()) / 86400000));
  const captains = gw.players.slice(0, 5);
  const diffs = gw.players.filter((p) => p.owned_pct < 10).slice(0, 6);
  const byPos = gw.players.filter((p) => p.pos === pos).slice(0, 8);

  const PlayerRow = ({ p, rank }) => (
    <Pressable onPress={() => { springy(); setExpanded(expanded === p.id ? null : p.id); }}>
      <View style={s.fplRow}>
        <Text style={[s.fplRank, TNUM]}>{rank}</Text>
        {p.photo ? <Image source={{ uri: p.photo }} style={s.fplFace} />
          : <View style={[s.fplFace, { backgroundColor: C.panel2 }]} />}
        <View style={{ flex: 1 }}>
          <Text style={s.fplName} numberOfLines={1}>
            {p.name}{p.status === "d" ? "  (doubtful)" : ""}
          </Text>
          <Text style={[s.optSub, TNUM]}>
            {p.team} {p.home ? "vs" : "at"} {p.opp} · £{p.price.toFixed(1)}m · owned {p.owned_pct}%
          </Text>
        </View>
        <View style={{ alignItems: "flex-end" }}>
          <View style={[s.ptsPill, { backgroundColor: rateColor(p.xpts, 5, 3) }]}>
            <Text style={[s.ptsPillTxt, TNUM]}>{p.xpts.toFixed(1)}</Text>
          </View>
          <Text style={s.tileLabel}>xPts</Text>
        </View>
      </View>
      {expanded === p.id && (
        <View style={s.fplBreak}>
          {Object.entries(p.breakdown).map(([k, v]) => (
            <View key={k} style={s.rowBetween}>
              <Text style={s.smallLabel}>{k.replace("_", " ")}</Text>
              <Text style={[s.smallLabel, TNUM, { color: v >= 0 ? C.dim : C.red }]}>
                {v >= 0 ? "+" : ""}{v}
              </Text>
            </View>
          ))}
          {!!p.news && <Text style={[s.axisNote, { color: C.amber }]}>{p.news}</Text>}
        </View>
      )}
    </Pressable>
  );

  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>Fantasy · {gw.name}</Text>
      <Text style={s.dimTxt}>
        Expected points for every player, adjusted for who they actually face this week
        using the match model. Tap a player to see where the points come from.
      </Text>
      <View style={s.tileRow}>
        <StatTile label="deadline" value={`${days}d`} sub={new Date(gw.deadline).toLocaleDateString()} />
        <StatTile label="fixtures" value={gw.fixtures.length} />
        <StatTile label="players ranked" value={gw.players.length} />
      </View>

      <Card>
        <SectionTitle icon="clipboard">The Plus100 team</SectionTitle>
        <Text style={s.dimTxt}>
          One persistent squad that plays by the real rules: it changes only through
          earned free transfers, never takes a points hit, and its actual score is
          tracked every round.
        </Text>
        <TiltIn><ModelTeam api={api} /></TiltIn>
      </Card>

      <Card>
        <SectionTitle icon="star">Captain picks <Text style={s.secNote}>doubled points</Text></SectionTitle>
        {captains.map((p, i) => <PlayerRow key={p.id} p={p} rank={i + 1} />)}
      </Card>

      <Card>
        <SectionTitle icon="users">Best by position</SectionTitle>
        <View style={s.segRow}>
          {["GK", "DEF", "MID", "FWD"].map((k) => (
            <Pressable key={k} onPress={() => setPos(k)} style={[s.segBtn, pos === k && s.segBtnOn]}>
              <Text style={[s.segTxt, pos === k && { color: C.onAccent, fontWeight: "700" }]}>{k}</Text>
            </Pressable>
          ))}
        </View>
        {byPos.map((p, i) => <PlayerRow key={p.id} p={p} rank={i + 1} />)}
      </Card>

      <Card>
        <SectionTitle icon="eye-off" note="under 10% owned">Differentials</SectionTitle>
        <Text style={s.dimTxt}>Strong projected scores that most managers don't have. When they pay off, you climb.</Text>
        {diffs.map((p, i) => <PlayerRow key={p.id} p={p} rank={i + 1} />)}
      </Card>

      <Card>
        <SectionTitle icon="user-check">My team</SectionTitle>
        {!fplId ? (
          <Text style={s.dimTxt}>
            Add your FPL team ID in Settings and this section grades your actual squad:
            projected points, best captain, weakest starter, and upgrade ideas.
          </Text>
        ) : teamErr ? (
          <Text style={s.dimTxt}>{teamErr}</Text>
        ) : !team ? (
          <BallLoader label="Grading your squad…" />
        ) : (
          <>
            <View style={s.tileRow}>
              <StatTile label={team.entry_name} value={team.projected_points} sub="projected pts" color={C.lime} />
              <StatTile label="best captain" value={team.best_captain ?? "–"} />
              <StatTile label="weakest starter" value={team.weakest_starter ?? "–"} color={C.amber} />
            </View>
            {team.transfer_advice && (
              <View style={[s.ctxNote, { marginTop: 12 }]}>
                <Feather name={team.transfer_advice.action === "transfer" ? "repeat" : "pause-circle"}
                  size={14} color={team.transfer_advice.action === "transfer" ? C.lime : C.muted} />
                <Text style={s.ctxNoteTxt}>
                  {"  "}
                  {team.transfer_advice.action === "transfer" && (
                    <Text style={{ fontWeight: "800", color: C.chalk }}>
                      {team.transfer_advice.out} → {team.transfer_advice.in}.{" "}
                    </Text>
                  )}
                  {team.transfer_advice.reason}
                  {team.advice_note ? ` ${team.advice_note}` : ""}
                </Text>
              </View>
            )}
            {team.squad.slice(0, 15).map((p, i) => <PlayerRow key={p.id} p={p} rank={i + 1} />)}
            {team.upgrade_ideas?.length > 0 && (
              <>
                <SectionTitle icon="trending-up">Upgrade ideas</SectionTitle>
                {team.upgrade_ideas.map((p, i) => <PlayerRow key={p.id} p={p} rank={i + 1} />)}
              </>
            )}
          </>
        )}
      </Card>

      <Text style={[s.axisNote, { marginTop: 12 }]}>{gw.note}</Text>
    </ScrollView>
  );
}

/* ================= settings ================= */
function SettingsScreen({ server, oddsFormat, bankroll, fplId, saveSettings, meta }) {
  const [showAbout, setShowAbout] = useState(false);
  const [fplDraft, setFplDraft] = useState(fplId);
  const [serverDraft, setServerDraft] = useState(server);
  const [bankrollDraft, setBankrollDraft] = useState(bankroll);

  const Seg = ({ options, value, onPick }) => (
    <View style={s.segRow}>
      {options.map(([k, label]) => (
        <Pressable key={k} onPress={() => onPick(k)}
          style={[s.segBtn, value === k && s.segBtnOn]}>
          <Text style={[s.segTxt, value === k && { color: C.onAccent, fontWeight: "700" }]}>{label}</Text>
        </Pressable>
      ))}
    </View>
  );
  const InfoRow = ({ label, value }) => (
    <View style={s.infoRow}>
      <Text style={s.smallLabel}>{label}</Text>
      <Text style={[s.probLabel, TNUM, { textAlign: "right", flexShrink: 1 }]}>{value}</Text>
    </View>
  );

  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>Settings</Text>

      <Card>
        <SectionTitle icon="hash">Odds format</SectionTitle>
        <Text style={s.dimTxt}>How prices are written throughout the app.</Text>
        <Seg options={[["decimal", "Decimal (2.50)"], ["american", "American (+150)"]]}
          value={oddsFormat} onPick={(v) => saveSettings({ oddsFormat: v })} />
      </Card>

      <Card>
        <SectionTitle icon="credit-card">Bankroll</SectionTitle>
        <Text style={s.dimTxt}>
          The total amount you have set aside for betting. Stake suggestions then show real
          dollar amounts instead of percentages. Stored only on this phone.
        </Text>
        <View style={[s.row, { marginTop: 12, gap: 10 }]}>
          <TextInput style={[s.input, { flex: 1 }]} value={bankrollDraft}
            onChangeText={setBankrollDraft} placeholder="e.g. 500" keyboardType="numeric"
            placeholderTextColor={C.muted} />
          <Pressable style={s.btn} onPress={() => saveSettings({ bankroll: bankrollDraft.trim() })}>
            <Text style={s.btnTxt}>Save</Text>
          </Pressable>
        </View>
      </Card>

      <Card>
        <SectionTitle icon="server">Prediction server</SectionTitle>
        <Text style={s.dimTxt}>
          Where the numbers come from. Your Mac's address on home Wi-Fi, or your cloud
          server's address to use the app anywhere.
        </Text>
        <View style={[s.row, { marginTop: 12, gap: 10 }]}>
          <TextInput style={[s.input, { flex: 1, fontSize: 14 }]} value={serverDraft}
            onChangeText={setServerDraft} autoCapitalize="none" autoCorrect={false}
            placeholder="http://10.0.0.218:8710" placeholderTextColor={C.muted} />
          <Pressable style={s.btn} onPress={() => saveSettings({ server: serverDraft.trim() })}>
            <Text style={s.btnTxt}>Save</Text>
          </Pressable>
        </View>
        <Text style={[s.axisNote, { marginTop: 8 }]}>
          current: {server}{meta ? "  ·  connected" : "  ·  not responding"}
        </Text>
      </Card>

      <Card>
        <SectionTitle icon="database">Data &amp; model</SectionTitle>
        <InfoRow label="results through" value={meta ? meta.data_to : "–"} />
        <InfoRow label="matches in database" value={meta ? meta.matches.toLocaleString() : "–"} />
        <InfoRow label="model accuracy (latest test)"
          value={meta?.live_eval ? `${(meta.live_eval.model_accuracy * 100).toFixed(1)}%` : "–"} />
        <InfoRow label="data refresh" value="automatic, every 6 hours" />
      </Card>

      <Card>
        <SectionTitle icon="heart">Play it safe</SectionTitle>
        <Text style={s.dimTxt}>
          Only bet money you can afford to lose, and take breaks. If it stops being fun,
          call 1-800-GAMBLER. Free, confidential, always open. The About tab has the full
          honesty page: what this app can and cannot know.
        </Text>
      </Card>

      <Card>
        <SectionTitle icon="award">Fantasy team ID</SectionTitle>
        <Text style={s.dimTxt}>
          Your FPL team's number. Find it on fantasy.premierleague.com: open the Points page
          and copy the number from the address bar (…/entry/1234567/…). The Fantasy tab then
          grades your actual squad every week.
        </Text>
        <View style={[s.row, { marginTop: 12, gap: 10 }]}>
          <TextInput style={[s.input, { flex: 1 }]} value={fplDraft}
            onChangeText={setFplDraft} placeholder="e.g. 1234567" keyboardType="numeric"
            placeholderTextColor={C.muted} />
          <Pressable style={s.btn} onPress={() => saveSettings({ fplId: fplDraft.replace(/\D/g, "") })}>
            <Text style={s.btnTxt}>Save</Text>
          </Pressable>
        </View>
      </Card>

      <Card>
        <SectionTitle icon="shield">About &amp; honesty page</SectionTitle>
        <Text style={s.dimTxt}>
          What this app is, its measured accuracy, its limits, and the no-liability terms.
        </Text>
        <Pressable style={[s.detailsBtn, { marginTop: 12 }]} onPress={() => setShowAbout(true)}>
          <Feather name="external-link" size={14} color={C.lime} />
          <Text style={s.detailsBtnTxt}>  Open</Text>
        </Pressable>
        <Modal visible={showAbout} animationType="slide" onRequestClose={() => setShowAbout(false)}>
          <AboutModalBody onClose={() => setShowAbout(false)} meta={meta} />
        </Modal>
      </Card>

      <Pressable style={[s.detailsBtn, { alignSelf: "center", marginTop: 24 }]}
        onPress={() => {
          AsyncStorage.removeItem(SETTINGS_KEY).catch(() => {});
          saveSettings({ server: DEFAULT_SERVER, oddsFormat: "decimal",
                         bankroll: "", fplId: "" });
          setServerDraft(DEFAULT_SERVER); setBankrollDraft(""); setFplDraft("");
        }}>
        <Feather name="rotate-ccw" size={14} color={C.dim} />
        <Text style={[s.detailsBtnTxt, { color: C.dim }]}>  Reset all settings</Text>
      </Pressable>
    </ScrollView>
  );
}

function AboutModalBody({ onClose, meta }) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.root, { paddingTop: Math.max(insets.top, 24) + 6, paddingBottom: insets.bottom }]}>
      <View style={s.rowBetween}>
        <Text style={[s.h2, { padding: 20, marginBottom: 0 }]}>About Plus100</Text>
        <Pressable onPress={onClose} style={{ padding: 20 }} accessibilityLabel="Close">
          <Feather name="x" size={22} color={C.chalk} />
        </Pressable>
      </View>
      <AboutScreen meta={meta} />
    </View>
  );
}

/* ================= about ================= */

function AboutScreen({ meta }) {
  const S = ({ title, children }) => (
    <View>
      <Text style={s.h3}>{title}</Text>
      <Text style={s.aboutTxt}>{children}</Text>
    </View>
  );
  const live = meta?.live_eval;
  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>What Plus100 is (and isn't)</Text>
      <Text style={s.aboutTxt}>
        Plus100 is a statistics tool. It estimates the probability of football outcomes from
        historical data and compares them against sportsbook prices. It is not a sportsbook,
        does not accept bets, and has no connection to any gambling operator.
      </Text>
      {live && (
        <View style={s.tileRow}>
          <StatTile label="matches tested" value={(live.matches ?? 0).toLocaleString()} />
          <StatTile label="model accuracy" value={`${(live.model_accuracy * 100).toFixed(1)}%`} color={C.lime} />
          <StatTile label="bookmakers" value={`${(live.book_accuracy * 100).toFixed(1)}%`} color={C.sky} />
        </View>
      )}
      <S title="Probabilities are not certainty">
        A 60% chance fails 4 times out of 10. Even the strongest "good bet" flagged here loses
        regularly. Edges only show up as profit across many bets. There is no 100% win rate,
        here or anywhere.
      </S>
      <S title="Measured, honest accuracy">
        {meta?.live_eval
          ? `Accuracy is re-measured automatically at every data refresh. In the latest window the model called ${(meta.live_eval.model_accuracy * 100).toFixed(1)}% of results correctly. For scale: random guessing on three outcomes gets 33%, and the bookmakers themselves get ${(meta.live_eval.book_accuracy * 100).toFixed(1)}%. `
          : "Accuracy is re-measured automatically at every data refresh. "}
        Probabilities are calibrated: when the model says 40%, it happens about 40% of the time.
      </S>
      <S title="How good the fantasy projections are">
        {meta?.fantasy_eval
          ? `Measured on ${meta.fantasy_eval.pairs} real player-seasons, judged against what those players actually scored the FOLLOWING season. Ranking players by shot quality alone scores ${meta.fantasy_eval.xg_only.toFixed(2)} out of 1; by their own scoring record alone ${meta.fantasy_eval.record_only.toFixed(2)}; the blend of both that this app uses, ${meta.fantasy_eval.blended.toFixed(2)}. `
          : ""}
        Fantasy is more predictable than match results, because a player's own
        record carries over from season to season in a way that single match
        outcomes never do.
      </S>
      <S title="Where edges really come from">
        Against closing prices nothing out-predicts the market, including this model.
        We tested that properly rather than assuming it: on thousands of unseen
        matches, feeding our model's view into the closing price made predictions
        slightly worse, not better. Edges
        come from disagreements BETWEEN books, soft early prices, and boosts. That
        is what the Vs Market tab hunts.
      </S>
      <S title="No liability">
        You alone decide whether, where, and how much to bet. Plus100 and its author accept no
        responsibility for any losses. Nothing here is financial advice. Use only where legal
        and only at legal age.
      </S>
      <S title="Bet responsibly">
        Never stake money you cannot afford to lose. ¼-Kelly stakes are caps, not targets.
        If gambling stops being fun, call 1-800-GAMBLER. Free, confidential, and open 24/7.
      </S>
    </ScrollView>
  );
}

/* ================= the math (details modal) ================= */
function DetailsModal({ visible, onClose, prediction, meta }) {
  const insets = useSafeAreaInsets();
  const p = prediction;
  if (!p) return null;
  const md = p.model_detail;
  const lH = p.expected_goals.home, lA = p.expected_goals.away;
  const eloMax = Math.max(p.home.elo, p.away.elo, 1);
  const m = p.markets.one_x_two;
  const live = meta?.live_eval;

  const gap = Math.round(Math.abs(md.elo_diff));
  const stronger = md.elo_diff >= 0 ? p.home.name : p.away.name;
  const gapWinPct = (100 / (1 + Math.pow(10, -Math.abs(md.elo_diff) / 400))).toFixed(0);
  const recentSays = `${md.dc[0]} to ${md.dc[1]}`;
  const ratingsSay = `${md.elo[0]} to ${md.elo[1]}`;
  const agree = Math.abs(md.dc[0] - md.elo[0]) < 0.25 && Math.abs(md.dc[1] - md.elo[1]) < 0.25;
  const top = p.markets.correct_scores[0];
  const p00 = p.score_matrix[0][0];
  const favSide = m.home >= m.away ? "home" : "away";
  const favName = favSide === "home" ? p.home.name : p.away.name;
  const favProb = Math.max(m.home, m.away);
  const favFair = (1 / favProb).toFixed(2);

  const Para = ({ children }) => <Text style={s.aboutTxt}>{children}</Text>;
  const Key = ({ children }) => <Text style={{ color: C.lime, fontWeight: "700" }}>{children}</Text>;

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={[s.root, { paddingTop: Math.max(insets.top, 24) + 6, paddingBottom: insets.bottom }]}>
        <View style={s.rowBetween}>
          <Text style={[s.h2, { padding: 20, marginBottom: 0 }]}>How we got these numbers</Text>
          <Pressable onPress={onClose} style={{ padding: 20 }} accessibilityLabel="Close">
            <Feather name="x" size={24} color={C.chalk} />
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={s.scroll}>

          <SectionTitle accent icon="bar-chart">Step 1 · How strong is each team?</SectionTitle>
          {[[p.home.name, p.home.elo, C.lime], [p.away.name, p.away.elo, C.sky]].map(([n, e, col]) => (
            <View key={n} style={{ marginTop: 8 }}>
              <View style={s.rowBetween}>
                <Text style={s.probLabel}>{n}</Text>
                <Text style={[s.probLabel, TNUM, { color: col }]}>{Math.round(e)}</Text>
              </View>
              <HBar frac={(e - 1300) / (eloMax + 100 - 1300)} color={col} />
            </View>
          ))}
          <Para>
            Every team carries a strength rating that goes up when they beat good opponents and
            down when they lose to weak ones. The rating is not a one-off number: it re-learns
            from every new result at each data refresh, and today's effective rating additionally
            discounts players who are missing right now. It is built from every result in our
            {" "}{meta ? meta.matches.toLocaleString() : "154,063"}-match database, with recent
            games counting the most.
          </Para>
          <Para>
            Here the gap is <Key>{gap} points in favor of {stronger}</Key>. Gaps like this one
            historically mean the stronger side gets the better of the matchup about
            {" "}<Key>{gapWinPct}%</Key> of the time before anything else is considered. This gap
            is the single biggest input to the prediction.
          </Para>

          <SectionTitle accent icon="users">Step 2 · Who can actually play?</SectionTitle>
          <Para>
            Before any goals are estimated, we assemble each side's probable players from
            the official squad lists, then remove anyone the league's live availability
            flags or the day's team news say is out or doubtful. A missing player takes
            his usual share of his team's goals with him; anyone excluded for this match
            is named at the bottom of this page, and the pitch view shows who remains.
          </Para>

          <SectionTitle accent icon="target">Step 3 · How many goals do we expect?</SectionTitle>
          <Para>
            We estimate scoring two independent ways. First, from <Key>recent play</Key>: what
            each team has actually scored and conceded lately{md.uses_xg ? ", weighted by the quality of the chances they created (xG), not just lucky bounces" : ""}.
            That view says {recentSays} goals. Second, from the <Key>rating gap</Key> in step 1,
            which says {ratingsSay}.
          </Para>
          <Para>
            {agree
              ? "The two views broadly agree here, which makes this a more trustworthy prediction than average."
              : "The two views disagree somewhat here. That usually means a team's recent results have been better or worse than its underlying strength, and it makes this prediction a little less certain than average."}
            {" "}We combine them, trusting the ratings view more (that weighting was fitted on
            10,000 past matches, not guessed), and land on <Key>{lH} goals for {p.home.name}</Key> and
            {" "}<Key>{lA} for {p.away.name}</Key>.

          </Para>

          <SectionTitle accent icon="grid">Step 4 · From goals to chances</SectionTitle>
          <Para>
            Goals in football arrive in streaks and droughts, so a team expected to score {lH} can
            easily score 0 or 3. We play this match out across <Key>every possible scoreline</Key>
            {" "}using the expected goals from step 2. For example, that math makes the single most
            likely score <Key>{top.score}, at {(top.prob * 100).toFixed(1)}%</Key>, and a 0-0
            about {(p00 * 100).toFixed(1)}%. (We also nudge low-scoring draws slightly upward,
            because real football produces more 0-0s and 1-1s than pure chance would.)
          </Para>
          <Para>
            Adding up every scoreline where {p.home.name} finishes ahead gives their
            {" "}<Key>{(m.home * 100).toFixed(1)}%</Key> win chance; the draws add to
            {" "}{(m.draw * 100).toFixed(1)}%, and {p.away.name} gets
            {" "}{(m.away * 100).toFixed(1)}%. Every other number in the app, from over/unders to
            parlays, comes from this same set of scorelines, which is why they never contradict
            each other.
          </Para>

          <SectionTitle accent icon="dollar-sign">Step 5 · Why this matters for betting</SectionTitle>
          <Para>
            A {(favProb * 100).toFixed(0)}% chance for {favName} converts to fair odds of
            {" "}<Key>{favFair}</Key>. If a sportsbook pays more than that, the price is in your
            favor; if it pays less, the bet loses money over time no matter how confident it
            feels. Comparing our numbers against live prices is the entire point of the Vs Market
            tab.
          </Para>

          <SectionTitle accent icon="check-circle">Step 6 · How much should you trust this?</SectionTitle>
          <Para>
            {live
              ? `We re-test the model automatically every time the data refreshes. In the latest test it called ${(live.model_accuracy * 100).toFixed(1)}% of ${live.matches.toLocaleString()} results correctly. For scale: random guessing gets 33%, and the bookmakers, who also know lineups and injuries, get ${(live.book_accuracy * 100).toFixed(1)}%. `
              : ""}
            Football is mostly luck in any single match, so treat these numbers as honest odds,
            never as promises. Availability is checked automatically (step 2), but confirmed
            team sheets only drop about an hour before kickoff and can still differ.
          </Para>
          {(p.caveats || []).map((c, i) => (
            <View key={i} style={s.caveatRow}>
              <Feather name="alert-triangle" size={13} color={C.amber} />
              <Text style={s.caveat}> {c}</Text>
            </View>
          ))}
        </ScrollView>
      </View>
    </Modal>
  );
}

/* ================= styles ================= */
const makeStyles = (C) => StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingVertical: 14, gap: 12 },
  logoMark: { width: 34, height: 34, borderRadius: 10, backgroundColor: C.lime,
    alignItems: "center", justifyContent: "center" },
  logoPlus: { color: C.onAccent, fontSize: 24, fontWeight: "800", marginTop: -2 },
  wordmark: { color: C.chalk, fontSize: 22, fontWeight: "800", letterSpacing: -0.4 },
  srvBanner: { flexDirection: "row", alignItems: "center", gap: 10, marginHorizontal: 20,
    marginBottom: 8, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 12,
    backgroundColor: C.panel, borderWidth: 1, borderColor: C.line },
  srvBannerTxt: { color: C.dim, fontSize: 13, lineHeight: 18, flex: 1 },
  nav: { flexDirection: "row", borderTopWidth: 1, borderTopColor: C.line,
    backgroundColor: C.bg, paddingHorizontal: 8, paddingTop: 6 },
  navBtn: { flex: 1, alignItems: "center", paddingVertical: 9, gap: 3, minHeight: 52,
    borderRadius: 13, marginHorizontal: 2 },
  navBtnOn: { backgroundColor: C.limeDim },
  navTxt: { color: C.muted, fontSize: 11, fontWeight: "600", letterSpacing: 0.2 },
  scroll: { padding: 20, paddingBottom: 56 },

  h2: { color: C.chalk, fontSize: 24, fontWeight: "800", letterSpacing: -0.5, marginBottom: 10 },
  h3: { color: C.chalk, fontSize: 15, fontWeight: "700", letterSpacing: 0.1 },
  secTitleRow: { flexDirection: "row", alignItems: "center", marginTop: 6, marginBottom: 13 },
  secNote: { color: C.muted, fontSize: 11.5, marginLeft: "auto" },
  dimTxt: { color: C.dim, fontSize: 14.5, lineHeight: 22 },
  smallLabel: { color: C.dim, fontSize: 13 },
  errTxt: { color: C.red, fontSize: 14, marginTop: 12, lineHeight: 21 },

  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 18,
    padding: 20, marginTop: 16 },
  cardGood: { borderColor: C.pitch, backgroundColor: C.pitchDim },
  tileRow: { flexDirection: "row", gap: 10, marginTop: 18 },
  tile: { flex: 1, backgroundColor: C.panel2, borderRadius: 14, paddingVertical: 14,
    paddingHorizontal: 8, alignItems: "center" },
  tileVal: { color: C.chalk, fontSize: 19, fontWeight: "700" },
  tileLabel: { color: C.muted, fontSize: 11, marginTop: 4, textAlign: "center" },
  tileSub: { color: C.dim, fontSize: 11 },

  slotRow: { flexDirection: "row", alignItems: "stretch", gap: 10 },
  slotCard: { flex: 1, backgroundColor: C.panel, borderWidth: 1.5, borderColor: C.line,
    borderRadius: 20, paddingVertical: 20, paddingHorizontal: 10, alignItems: "center",
    justifyContent: "center", minHeight: 136 },
  slotCardOn: { borderColor: C.lime, backgroundColor: C.limeDim },
  slotBadge: { width: 54, height: 54, borderRadius: 27 },
  slotBadgePh: { width: 54, height: 54, borderRadius: 27, backgroundColor: C.panel2,
    alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: C.line },
  slotName: { color: C.chalk, fontSize: 15, fontWeight: "700", marginTop: 10,
    textAlign: "center" },
  slotSub: { color: C.muted, fontSize: 12, marginTop: 3 },
  vsWrap: { alignItems: "center", justifyContent: "center", width: 52 },
  vsTxt: { color: C.dim, fontSize: 17, fontWeight: "900", fontStyle: "italic",
    letterSpacing: 1 },
  cta: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 9,
    borderRadius: 16, minHeight: 54, marginTop: 14, overflow: "hidden" },
  ctaTxt: { color: C.onAccent, fontSize: 16.5, fontWeight: "800", letterSpacing: 0.2 },

  heroCard: { borderColor: "#0F6B33", overflow: "hidden", backgroundColor: "#0E713A" },
  /* negative insets cancel the card's 20px padding: RN positions absolute children
     inside the padding box on native, so 0-insets leave an uncovered frame there */
  heroPhoto: { position: "absolute", left: -20, right: -20, top: -20, bottom: -20,
    resizeMode: "cover" },
  heroShade: { position: "absolute", left: -20, right: -20, top: -20, bottom: -20,
    backgroundColor: "rgba(7,18,10,0.72)" },
  heroRow: { flexDirection: "row", alignItems: "flex-start", gap: 6 },
  heroCol: { flex: 1, alignItems: "center" },
  heroBadge: { width: 52, height: 52, borderRadius: 26, marginBottom: 8 },
  heroBadgePh: { backgroundColor: "rgba(255,255,255,0.16)", alignItems: "center",
    justifyContent: "center" },
  heroName: { color: HERO.txt, fontSize: 13.5, fontWeight: "700", textAlign: "center" },
  heroPct: { fontSize: 27, fontWeight: "900", marginTop: 3 },
  heroDrawLbl: { color: HERO.dim, fontSize: 10.5, letterSpacing: 2, marginTop: 20 },
  heroDraw: { color: HERO.draw, fontSize: 21, fontWeight: "800", marginTop: 6 },
  heroFair: { color: HERO.dim, fontSize: 12, marginTop: 3 },
  heroTile: { flex: 1, backgroundColor: "rgba(255,255,255,0.13)", borderRadius: 14,
    paddingVertical: 12, paddingHorizontal: 6, alignItems: "center" },
  heroTileVal: { fontSize: 17, fontWeight: "800" },
  heroTileLbl: { color: HERO.dim, fontSize: 10.5, marginTop: 4, textAlign: "center" },
  heroDetailsBtn: { flexDirection: "row", alignItems: "center", marginTop: 14,
    borderWidth: 1, borderColor: "rgba(255,255,255,0.35)", borderRadius: 12,
    paddingHorizontal: 16, paddingVertical: 12, alignSelf: "flex-start", minHeight: 46 },
  heroDetailsTxt: { color: HERO.txt, fontSize: 13.5, fontWeight: "700" },

  banner: { flexDirection: "row", alignItems: "center", borderRadius: 20, padding: 18,
    marginBottom: 18, overflow: "hidden", minHeight: 116 },
  bannerKicker: { color: "#CFF5D6", fontSize: 10.5, fontWeight: "800", letterSpacing: 2.2 },
  bannerTitle: { color: "#FFFFFF", fontSize: 20, fontWeight: "900", letterSpacing: -0.4,
    marginTop: 5, lineHeight: 25 },
  bannerSub: { color: "#D9F4DF", fontSize: 12.5, marginTop: 7 },

  mqCard: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 16,
    paddingHorizontal: 14, paddingVertical: 12, width: 168 },
  mqTag: { color: C.lime, fontSize: 11, fontWeight: "800", letterSpacing: 0.3 },
  mqBadge: { width: 28, height: 28, borderRadius: 14 },
  mqBadgePh: { backgroundColor: C.panel2, alignItems: "center", justifyContent: "center" },
  mqVs: { color: C.muted, fontSize: 11, fontWeight: "700", fontStyle: "italic" },
  mqNames: { color: C.dim, fontSize: 11, marginTop: 8 },


  dotWrap: { width: 44, height: 44, borderRadius: 22, borderWidth: 2,
    backgroundColor: "rgba(7,10,20,0.55)", alignItems: "center", justifyContent: "center",
    overflow: "hidden" },
  dotImg: { width: 40, height: 40, borderRadius: 20 },
  dotInitials: { color: C.chalk, fontSize: 14, fontWeight: "800" },
  dotName: { color: "#FFFFFF", fontSize: 9.5, fontWeight: "700", marginTop: 3,
    textShadowColor: "rgba(0,0,0,0.85)", textShadowRadius: 3, textShadowOffset: { width: 0, height: 1 } },
  dotBadge: { position: "absolute", top: -5, right: -12, minWidth: 24, height: 16,
    borderRadius: 8, paddingHorizontal: 4, alignItems: "center", justifyContent: "center",
    zIndex: 3, borderWidth: 1, borderColor: "rgba(255,255,255,0.7)" },
  dotBadgeTxt: { color: "#FFFFFF", fontSize: 9, fontWeight: "800" },
  ptsPill: { borderRadius: 9, paddingHorizontal: 8, paddingVertical: 3, minWidth: 40,
    alignItems: "center" },
  ptsPillTxt: { color: "#FFFFFF", fontSize: 14.5, fontWeight: "800" },
  xiTeam: { color: C.chalk, fontSize: 13.5, fontWeight: "700", maxWidth: 130 },
  xiSelRow: { flexDirection: "row", alignItems: "center", backgroundColor: C.panel2,
    borderRadius: 14, padding: 12, marginTop: 12 },
  xiSelImg: { width: 46, height: 46, borderRadius: 23 },
  xiScoreLbl: { color: C.muted, fontSize: 11 },
  benchRow: { flexDirection: "row", alignItems: "center", marginTop: 12, gap: 6,
    backgroundColor: C.panel2, borderRadius: 14, padding: 10 },
  benchImg: { width: 34, height: 34, borderRadius: 17 },
  benchName: { color: C.dim, fontSize: 9.5, marginTop: 2, maxWidth: 54 },
  fplFace: { width: 32, height: 32, borderRadius: 16 },
  input: { backgroundColor: C.panel2, borderRadius: 12, borderWidth: 1, borderColor: C.line,
    color: C.chalk, fontSize: 17, paddingHorizontal: 16, paddingVertical: 14, minHeight: 48 },
  dropdown: { backgroundColor: C.panel, borderRadius: 12, borderWidth: 1, borderColor: C.line, marginTop: 6 },
  opt: { paddingHorizontal: 16, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: C.line,
    minHeight: 48 },
  optName: { color: C.chalk, fontSize: 16, fontWeight: "600" },
  optSub: { color: C.muted, fontSize: 13, marginTop: 4 },

  row: { flexDirection: "row", alignItems: "center" },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  rowWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  btn: { backgroundColor: C.lime, borderRadius: 14, paddingHorizontal: 24, paddingVertical: 14,
    minHeight: 48, justifyContent: "center" },
  btnOff: { opacity: 0.4 },
  btnTxt: { color: C.onAccent, fontWeight: "700", letterSpacing: 0.2, fontSize: 15.5 },

  ctxNote: { flexDirection: "row", alignItems: "flex-start", marginTop: 10,
    backgroundColor: C.panel2, borderRadius: 12, padding: 12 },
  ctxNoteTxt: { color: C.dim, fontSize: 12.5, lineHeight: 18, flex: 1 },

  swapBtn: { alignItems: "center", justifyContent: "center", alignSelf: "center",
    marginTop: 8, width: 36, height: 36, borderWidth: 1, borderColor: C.line,
    borderRadius: 999, backgroundColor: C.panel },
  slotSideTag: { alignSelf: "center", backgroundColor: "rgba(23,165,75,0.12)",
    borderWidth: 1, borderColor: C.lime, borderRadius: 999,
    paddingHorizontal: 10, paddingVertical: 2, marginBottom: 8 },
  slotSideTxt: { color: "#0C7A36", fontSize: 10, fontWeight: "800", letterSpacing: 1.6 },
  heroSideTag: { alignSelf: "center", backgroundColor: "rgba(255,255,255,0.16)",
    borderRadius: 999, paddingHorizontal: 9, paddingVertical: 2, marginTop: 3 },
  heroSideTxt: { color: "rgba(255,255,255,0.85)", fontSize: 9, fontWeight: "800",
    letterSpacing: 1.8 },

  probLabel: { color: C.chalk, fontSize: 15 },
  dot: { width: 9, height: 9, borderRadius: 5 },
  detailsBtn: { flexDirection: "row", alignItems: "center", marginTop: 18,
    borderWidth: 1, borderColor: C.line, borderRadius: 12, paddingHorizontal: 16,
    paddingVertical: 13, alignSelf: "flex-start", backgroundColor: C.panel2, minHeight: 48 },
  detailsBtnTxt: { color: C.lime, fontSize: 14, fontWeight: "700" },

  barTrack: { backgroundColor: C.panel2, overflow: "hidden", flex: 1 },
  pairRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 7 },
  pairTag: { color: C.muted, fontSize: 11.5, width: 64, letterSpacing: 0.2 },
  pairVal: { color: C.chalk, fontSize: 13, width: 40, textAlign: "right" },

  chip: { backgroundColor: C.panel2, borderWidth: 1, borderColor: C.line, borderRadius: 12,
    paddingHorizontal: 16, paddingVertical: 9, alignItems: "center" },
  chipTop: { color: C.chalk, fontWeight: "700", fontSize: 16 },
  chipSub: { color: C.muted, fontSize: 12 },

  heatRow: { flexDirection: "row", gap: 4, marginBottom: 4 },
  heatCell: { flex: 1, aspectRatio: 1.3, borderRadius: 6, alignItems: "center", justifyContent: "center" },
  heatHdr: { flex: 1, aspectRatio: 1.3, alignItems: "center", justifyContent: "center" },
  heatHdrTxt: { color: C.muted, fontSize: 11 },
  heatTxt: { fontSize: 10.5, fontWeight: "700" },
  axisNote: { color: C.muted, fontSize: 11.5, marginTop: 10, lineHeight: 16 },
  legendTxt: { color: C.dim, fontSize: 11.5 },

  mktRow: { flexDirection: "row", alignItems: "center", marginBottom: 12 },
  mktLabel: { color: C.chalk, fontSize: 13.5, width: 140 },
  mktVal: { color: C.dim, fontSize: 13, width: 38, textAlign: "right" },
  mktFair: { color: C.sky, fontSize: 13, width: 46, textAlign: "right", fontWeight: "600" },

  scorerRow: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 12 },
  scorerName: { color: C.chalk, fontSize: 14, width: 140 },
  scorerP: { color: C.dim, fontSize: 13, width: 36, textAlign: "right" },

  meetRow: { flexDirection: "row", alignItems: "center", paddingVertical: 10,
    borderTopWidth: 1, borderTopColor: C.line },
  meetDate: { color: C.muted, fontSize: 12, width: 62 },
  meetMatch: { color: C.dim, fontSize: 13.5, flex: 1 },
  meetScore: { color: C.chalk, fontSize: 14, fontWeight: "700", width: 40, textAlign: "right" },

  fchip: { width: 30, height: 30, borderRadius: 8, alignItems: "center", justifyContent: "center" },
  fchipTxt: { color: C.onAccent, fontSize: 13, fontWeight: "800" },

  caveatRow: { flexDirection: "row", alignItems: "flex-start", marginTop: 12, paddingRight: 12 },
  caveat: { color: C.amber, fontSize: 13.5, lineHeight: 20, flex: 1 },

  betOutcome: { color: C.chalk, fontSize: 16, fontWeight: "700", flexShrink: 1 },
  betLine: { color: C.dim, fontSize: 13.5, marginTop: 8 },
  noValTxt: { color: C.muted, fontSize: 13, fontWeight: "600" },
  edgeChip: { flexDirection: "row", alignItems: "center", backgroundColor: C.pitch,
    borderRadius: 8, paddingHorizontal: 10, paddingVertical: 5 },
  edgeChipTxt: { color: C.onAccent, fontSize: 13, fontWeight: "800" },
  suggName: { color: C.lime, fontSize: 12.5, fontWeight: "700", letterSpacing: 0.4, marginBottom: 6 },

  emVal: { fontSize: 12.5, fontWeight: "700", textAlign: "center", marginBottom: 4 },
  emTrack: { height: 7, borderRadius: 3.5, backgroundColor: C.panel2, position: "relative" },
  emZero: { position: "absolute", left: "50%", top: -2, bottom: -2, width: 1, backgroundColor: C.muted },
  emDot: { position: "absolute", top: -2, width: 11, height: 11, borderRadius: 5.5,
    marginLeft: -5.5, borderWidth: 2, borderColor: C.bg },
  emScale: { color: C.muted, fontSize: 10.5 },

  segRow: { flexDirection: "row", gap: 8, marginTop: 12 },
  segBtn: { flex: 1, paddingVertical: 12, borderRadius: 12, borderWidth: 1, borderColor: C.line,
    backgroundColor: C.panel2, alignItems: "center", minHeight: 44 },
  segBtnOn: { backgroundColor: C.lime, borderColor: C.lime },
  segTxt: { color: C.dim, fontSize: 13.5, fontWeight: "600" },
  infoRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingVertical: 9, borderTopWidth: 1, borderTopColor: C.line, gap: 12 },
  fplRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 10,
    borderTopWidth: 1, borderTopColor: C.line },
  fplRank: { color: C.muted, fontSize: 12, width: 18, textAlign: "center" },
  fplName: { color: C.chalk, fontSize: 15, fontWeight: "600" },
  fplBreak: { backgroundColor: C.panel2, borderRadius: 12, padding: 14, marginBottom: 8, gap: 5 },
  aboutTxt: { color: C.chalk, fontSize: 15, lineHeight: 23, marginTop: 8, marginBottom: 10 },
});
let s = makeStyles(C);
