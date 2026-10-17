/* Plus100 mobile: quantitative decision engine (Expo Go, latest SDK).
   Data-dense dashboard: probability visuals, real formulas with this match's
   numbers, H2H / form / Elo-history analytics. Backend: FastAPI on Mac or
   cloud (server address editable in Settings). */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Feather, Ionicons } from "@expo/vector-icons";
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo, ActivityIndicator, Animated, Easing, Image, KeyboardAvoidingView,
  LayoutAnimation, Modal, Platform, Pressable, ScrollView, StatusBar, StyleSheet, Switch,
  Text, TextInput, View,
} from "react-native";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";
import Svg, { Circle, Defs, G, Line, LinearGradient as SvgGrad, Polyline, Rect,
  Stop, Text as SvgText } from "react-native-svg";

const DEFAULT_SERVER = "https://plus100.onrender.com";

/* Matchday, one standard mode: bright, airy, grass-green and alive, the feel of
   a summer World Cup afternoon, not a studio at midnight.
   Semantic slots (names stable across the codebase):
     lime  = primary accent: our model, actions, active states (grass green)
     sky   = the market / bookmakers / away side (kit blue)
     pitch = positive value, wins, confirmed edge (deep green)
     red   = losses, negative edge   amber = caution */
const C_FOOTBALL = {
  bg: "#F2F7F0", panel: "#FFFFFF", panel2: "#EAF2E6", line: "#D9E5D4",
  chalk: "#16251A", dim: "#49604C", muted: "#7C917E",
  lime: "#17A54B", limeDim: "rgba(23,165,75,0.12)", limeDeep: "#0C7A36",
  red: "#E23A50", amber: "#D97E06", sky: "#2D7FF0",
  pitch: "#0C8A3E", pitchDim: "rgba(12,138,62,0.10)", pitchDeep: "#0A5B33",
  onAccent: "#FFFFFF", statusBar: "dark-content", sport: "football",
  heroBg: "#0E713A", heroBorder: "#0F6B33", bannerKicker: "#CFF5D6", bannerSub: "#D9F4DF",
  loaderShadow: "#1A2B1E", secIcon: "#7C917E",
  // the two grass gradients: welcome banner + CTA, and the hero fallback
  gradFrom: "#1CB258", gradTo: "#0B7A38", heroFrom: "#188F46", heroTo: "#0A6B31",
};
/* Basketball: an arena scoreboard after the lights go down. Near-black hardwood
   charcoal, warm off-white ink, ONE orange accent, hairline structure. Every slot
   is remapped so no grass green reaches a basketball screen. */
let C = C_FOOTBALL;
const CB = { ...C_FOOTBALL, sport: "basketball",
  bg: "#0C0D11", panel: "#15171D", panel2: "#1C1F26", line: "rgba(255,255,255,0.08)",
  chalk: "#F4F1EC", dim: "#B5AFA6", muted: "#8A857D",
  lime: "#FF7A1A", limeDim: "rgba(255,122,26,0.16)", limeDeep: "#FF9A4D",
  red: "#FF5C6C", amber: "#FFC46B", sky: "#6FB1FF",
  pitch: "#FF7A1A", pitchDim: "rgba(255,122,26,0.10)", pitchDeep: "#FF9A4D",
  onAccent: "#140A04", statusBar: "light-content",
  heroBg: "#111318", heroBorder: "rgba(255,255,255,0.10)", bannerKicker: "#FF9A4D", bannerSub: "#B5AFA6",
  loaderShadow: "#F4F1EC", secIcon: "#FF7A1A",
};
// condensed numerals for the scoreboard; system faces, so nothing to download
const NUM = { fontFamily: Platform.select({ ios: "Avenir Next Condensed", android: "sans-serif-condensed", default: undefined }), fontWeight: "700" };
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
  if (!a || colorDist(h, a) < 95) a = colorDist(h, fbA) < 95 ? C.amber : fbA;
  return [h, a];
}

/* rating pills: green = strong, amber = decent, grey = background */
const rateColor = (v, good, ok) => (v >= good ? C.pitch : v >= ok ? C.amber : C.muted);

// Read once at startup and follow the switch while the app is open. The module
// flag is read at animation time, so no re-render is needed when it flips.
let REDUCE_MOTION = false;
try {
  const p = AccessibilityInfo?.isReduceMotionEnabled?.();
  if (p && typeof p.then === "function") {
    p.then((v) => { REDUCE_MOTION = !!v; }).catch(() => {});
  }
  AccessibilityInfo?.addEventListener?.("reduceMotionChanged", (v) => { REDUCE_MOTION = !!v; });
} catch { /* motion preference is optional; never block startup for it */ }
const springy = () => LayoutAnimation.configureNext(
  LayoutAnimation.create(REDUCE_MOTION ? 1 : 220, LayoutAnimation.Types.easeInEaseOut,
    LayoutAnimation.Properties.opacity));

// kickoff ISO (offset-carrying) -> the viewer's local "DD/MM · HH:MM"
function fmtKickoff(iso, tbc) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const two = (n) => String(n).padStart(2, "0");
  const day = `${two(d.getDate())}/${two(d.getMonth() + 1)}`;
  return tbc ? `${day} · time TBC` : `${day} · ${two(d.getHours())}:${two(d.getMinutes())}`;
}

// Root sets this from the oddsFormat setting on every render; fmtOdds reads it.
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
  const [sport, setSport] = useState("football");
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState("predict");
  const [home, setHome] = useState(null);
  const [away, setAway] = useState(null);
  const [neutral, setNeutral] = useState(false);  // auto-set on team pick
  const [prediction, setPrediction] = useState(null);
  const [h2h, setH2h] = useState(null);
  const [showDetails, setShowDetails] = useState(false);
  const [meta, setMeta] = useState(null);
  const [nbaMeta, setNbaMeta] = useState(null);
  // Basketball pick and results live here so switching tabs (which remounts the
  // screen) never loses them. date = the fixture's kickoff when loaded from the rail.
  const [nba, setNba] = useState({ home: null, away: null, neutral: false, date: null,
    prediction: null, props: null, propsMarket: null });
  const patchNba = (patch) => setNba((cur) => ({ ...cur, ...patch }));
  // Fetched payloads that should survive a tab switch: fixtures rails, NBA teams,
  // parlays, Vs Market scans, fantasy. Keyed by string; cleared when the server changes.
  const [cache, setCache] = useState({});
  const cacheSet = (key, value) => setCache((cur) => ({ ...cur, [key]: value }));
  useEffect(() => { setCache({}); }, [server]);

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
          fetchT(base + "/api/nba/meta", 30000).then((m) => m.json())
            .then((m) => { if (alive) setNbaMeta(m); }).catch(() => {});
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
          if (sv.oddsFormat) setOddsFormat(sv.oddsFormat);
          if (sv.bankroll != null) setBankroll(String(sv.bankroll));
          if (sv.fplId != null) setFplId(String(sv.fplId));
          if (sv.sport === "basketball" || sv.sport === "football") setSport(sv.sport);
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
    const merged = { server, oddsFormat, bankroll, fplId, sport, ...patch };
    AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(merged)).catch(() => {});
    if (patch.server != null) setServer(patch.server);
    if (patch.oddsFormat != null) setOddsFormat(patch.oddsFormat);
    if (patch.bankroll != null) setBankroll(patch.bankroll);
    if (patch.fplId != null) setFplId(patch.fplId);
    if (patch.sport != null) setSport(patch.sport);
  };
  // Wipe the stored key and return every setting to its default, sport included.
  // Nothing is written back, so a fresh launch starts clean.
  const resetSettings = () => {
    AsyncStorage.removeItem(SETTINGS_KEY).catch(() => {});
    setServer(DEFAULT_SERVER); setOddsFormat("decimal"); setBankroll(""); setFplId("");
    setSport("football");
  };

  const shared = {
    api, home, away, setHome, setAway, neutral, setNeutral,
    prediction, setPrediction, h2h, setH2h, meta, nbaMeta, nba, patchNba, cache, cacheSet,
    server, srv, oddsFormat, bankroll, fplId, saveSettings, resetSettings, sport,
    openDetails: () => setShowDetails(true),
  };

  const TABS = [
    ["predict", "crosshair", "Predict"],
    ["bets", "bar-chart-2", "Vs Market"],
    ...(sport === "basketball" ? [] : [["parlays", "layers", "Parlays"], ["fpl", "award", "Fantasy"]]),
    ["settings", "settings", "Settings"],
  ];
  const switchSport = (sp) => {
    if (sp === sport) return;
    springy(); saveSettings({ sport: sp });
    if (sp === "basketball" && (tab === "parlays" || tab === "fpl")) setTab("predict");
  };

  C = sport === "basketball" ? CB : C_FOOTBALL;     // live theme for every screen below
  s = sport === "basketball" ? sb : s0;
  ODDS_FMT = oddsFormat;
  if (!ready) return <View style={s.root} />;
  return (
    <View style={[s.root, { paddingTop: insets.top + 6, paddingBottom: insets.bottom }]}>
      <StatusBar barStyle={C.statusBar} backgroundColor={C.bg} />
      <View style={s.header}>
        <View style={s.row}>
          <View style={s.logoMark}><Text style={s.logoPlus}>+</Text></View>
          <Text style={[s.wordmark, { marginLeft: 10 }]}>Plus100</Text>
        </View>
        <View style={s.sportSw} accessibilityRole="tablist">
          {[["football", "Football"], ["basketball", "Basketball"]].map(([k, label]) => (
            <Pressable key={k} onPress={() => switchSport(k)} accessibilityRole="tab" hitSlop={8}
              accessibilityState={{ selected: sport === k }}
              style={[s.sportBtn, sport === k && s.sportBtnOn]}>
              <Text style={[s.sportTxt, sport === k && s.sportTxtOn]}>{label}</Text>
            </Pressable>
          ))}
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
        {tab === "predict" && (sport === "basketball" ? <NbaPredictScreen {...shared} /> : <PredictScreen {...shared} />)}
        {tab === "bets" && <BestBetsScreen {...shared} />}
        {tab === "parlays" && <ParlaysScreen {...shared} />}
        {tab === "fpl" && <FPLScreen {...shared} />}
        {tab === "settings" && <SettingsScreen {...shared} />}
      </TabFade>

      <View style={s.nav}>
        {TABS.map(([k, icon, label]) => (
          <Pressable key={k} style={[s.navBtn, tab === k && s.navBtnOn]} onPress={() => setTab(k)}
            accessibilityRole="tab" accessibilityLabel={label} accessibilityState={{ selected: tab === k }}>
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

function GradientBG({ id, from, to, style, rounded = 18 }) {
  return (
    <View pointerEvents="none"
      style={[{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0,
        borderRadius: rounded, overflow: "hidden" }, style]}>
      <Svg width="100%" height="100%">
        <Defs>
          <SvgGrad id={id} x1="0" y1="0" x2="1" y2="1">
            <Stop offset="0" stopColor={from} />
            <Stop offset="1" stopColor={to} />
          </SvgGrad>
        </Defs>
        <Rect x="0" y="0" width="100%" height="100%" fill={`url(#${id})`} />
      </Svg>
    </View>
  );
}

function CountUp({ value, decimals = 1, suffix = "%", style }) {
  const [shown, setShown] = useState(REDUCE_MOTION ? value : 0);
  const a = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (REDUCE_MOTION) { setShown(value); return; }
    a.setValue(0);
    const id = a.addListener(({ value: t }) => setShown(t * value));
    Animated.timing(a, { toValue: 1, duration: 900,
      easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
    return () => a.removeListener(id);
  }, [value]);
  return <Text style={[TNUM, style]}>{Number(shown).toFixed(decimals)}{suffix}</Text>;
}

function PressScale({ onPress, style, containerStyle, children, disabled, accessibilityLabel }) {
  const a = useRef(new Animated.Value(1)).current;
  const to = (v, fr, tn) => Animated.spring(a, { toValue: v, friction: fr, tension: tn,
    useNativeDriver: true }).start();
  return (
    <Pressable disabled={disabled} onPress={onPress}
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
      <Feather name={icon} size={accent ? 16 : 14} color={accent ? C.lime : C.secIcon} />
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
        <Ionicons name={C.sport === "basketball" ? "basketball" : "football"} size={36} color={C.sport === "basketball" ? C.lime : C.chalk} />
      </Animated.View>
      <Animated.View style={{ width: 28, height: 5, borderRadius: 3, backgroundColor: C.loaderShadow,
        marginTop: 5,
        transform: [{ scaleX: y.interpolate({ inputRange: [0, 1], outputRange: [0.45, 1.15] }) }],
        opacity: y.interpolate({ inputRange: [0, 1], outputRange: [0.15, 0.4] }) }} />
      {label ? <Text style={[s.dimTxt, { marginTop: 14, textAlign: "center" }]}>{label}</Text> : null}
    </View>
  );
}

function TiltIn({ children }) {
  const a = useRef(new Animated.Value(REDUCE_MOTION ? 1 : 0)).current;
  useEffect(() => {
    Animated.timing(a, { toValue: 1, duration: 680,
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

/* one market line: label (wraps to two lines), bar, %, optional fair price.
   `max` rescales the bar so a family of rows peaks at full width. */
function MarketRow({ label, prob, color, max = 1, fair, height = 6 }) {
  return (
    <View style={s.mktRow}>
      <Text style={s.mktLabel} numberOfLines={2}>{label}</Text>
      <View style={s.mktBar}><HBar frac={prob / max} color={color} height={height} /></View>
      <Text style={[s.mktVal, TNUM]}>{(prob * 100).toFixed(0)}%</Text>
      {fair !== undefined && <Text style={[s.mktFair, TNUM]}>{fmtOdds(fair)}</Text>}
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
  let maxP = 0;   // normalize over the visible 6x6 so shading matches the web
  matrix.slice(0, N).forEach((row) => row.slice(0, N).forEach((v) => { maxP = Math.max(maxP, v); }));
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

function EloLineChart({ histHome, histAway, nameHome, nameAway, h = 130 }) {
  const [w, setW] = useState(0);
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
    <View onLayout={(e) => setW(e.nativeEvent.layout.width)}>
      {w > 0 && (
      <Svg width={w} height={h}>
        {[min + 20, (min + max) / 2, max - 20].map((v, i) => {
          const y = py + (1 - (v - min) / (max - min)) * (h - py * 2 - 14);
          return (
            <G key={i}>
              <Line x1={px} y1={y} x2={w - 6} y2={y} stroke={C.line} strokeWidth="0.5" />
              <SvgText x={2} y={y + 4} fill={C.muted} fontSize="12">{Math.round(v)}</SvgText>
            </G>
          );
        })}
        <Polyline points={pts(histHome)} fill="none" stroke={C.lime} strokeWidth="2" />
        <Polyline points={pts(histAway)} fill="none" stroke={C.sky} strokeWidth="2" />
      </Svg>
      )}
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
      <Text style={s.slotName} numberOfLines={2} adjustsFontSizeToFit>{team ? team.name : `Pick ${label.toLowerCase()}`}</Text>
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

function XILegend({ team, color }) {
  return (
    <View style={s.row}>
      <View style={[s.dot, { backgroundColor: color, borderWidth: 1, borderColor: C.line }]} />
      <View style={{ marginLeft: 7, flexShrink: 1 }}>
        <Text style={s.xiTeam} numberOfLines={1}>{team.name}</Text>
        <Text style={[s.optSub, TNUM, { marginTop: 1 }]}>
          {team.complete ? team.formation : `probable core · ${team.known} of 11 known`}
        </Text>
      </View>
    </View>
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
  return (
    <View>
      <View style={s.rowBetween}>
        <XILegend team={data.home} color={ring.home} />
        <XILegend team={data.away} color={ring.away} />
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

/* ================= cached fetches ================= */

// Fetch once per key and keep the payload in Root's cache, so a tab switch
// re-renders from memory instead of refetching. A rejection stores { error }.
function useCached(cache, cacheSet, key, load) {
  const val = key ? cache[key] : undefined;
  useEffect(() => {
    if (!key || val !== undefined) return;
    let alive = true;
    load().then((d) => { if (alive) cacheSet(key, d ?? null); })
      .catch((e) => { if (alive) cacheSet(key, { error: String(e.message) }); });
    return () => { alive = false; };
  }, [key, val === undefined]);
  return val;
}

/* ================= live upcoming fixtures ================= */

// One horizontal rail for both sports; the card is the sport's business.
function FixturesRail({ title, note, rows, renderCard }) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return (
    <View style={{ marginTop: 22 }}>
      <SectionTitle icon="calendar" note={note}>{title}</SectionTitle>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 10, paddingRight: 8 }}>
        {rows.map(renderCard)}
      </ScrollView>
    </View>
  );
}

const fixtureTeam = (f, side) =>
  ({ id: f[`${side}_id`], name: f[side], elo: f[`${side}_elo`], scope: "club", league: f.league });

function FootballFixtureCard({ f, onPress }) {
  const when = fmtKickoff(f.kickoff, f.time_tbc);
  return (
    <PressScale style={s.mqCard} onPress={onPress} accessibilityLabel={`${f.home} v ${f.away}, ${when}`}>
      <Text style={s.mqTag} numberOfLines={1}>{f.league}</Text>
      <Text style={[s.optName, { marginTop: 6, fontSize: 13.5 }]} numberOfLines={1}>{f.home}</Text>
      <Text style={[s.optName, { fontSize: 13.5 }]} numberOfLines={1}>v {f.away}</Text>
      <Text style={[s.mqNames, TNUM]} numberOfLines={1}>{when}</Text>
    </PressScale>
  );
}

/* ================= marquee matchups ================= */

// The biggest games on the real schedule: the fixtures rows already fetched,
// ranked by the two sides' combined rating. No extra requests.
function MarqueeRow({ rows, onLoad }) {
  const top = useMemo(() => (Array.isArray(rows) ? rows : [])
    .filter((f) => f.home_elo != null && f.away_elo != null)
    .sort((a, b) => (b.home_elo + b.away_elo) - (a.home_elo + a.away_elo)).slice(0, 6), [rows]);
  if (top.length === 0) return null;
  return (
    <View style={{ marginTop: 22 }}>
      <SectionTitle icon="star" note="highest combined rating">Marquee matchups</SectionTitle>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 10, paddingRight: 8 }}>
        {top.map((f) => (
          <PressScale key={`${f.home_id}:${f.away_id}`} style={s.mqCard}
            onPress={() => onLoad(fixtureTeam(f, "home"), fixtureTeam(f, "away"))}
            accessibilityLabel={`${f.home} v ${f.away}`}>
            <Text style={[s.mqTag, TNUM]} numberOfLines={1}>Elo {Math.round(f.home_elo)} v {Math.round(f.away_elo)}</Text>
            <Text style={[s.optName, { marginTop: 6, fontSize: 13.5 }]} numberOfLines={1}>{f.home}</Text>
            <Text style={[s.optName, { fontSize: 13.5 }]} numberOfLines={1}>v {f.away}</Text>
            <Text style={[s.mqNames, TNUM]} numberOfLines={1}>{fmtKickoff(f.kickoff, f.time_tbc)}</Text>
          </PressScale>
        ))}
      </ScrollView>
    </View>
  );
}

/* ================= predict screen ================= */
function PredictScreen(props) {
  const { api, home, away, setHome, setAway, neutral, setNeutral, meta,
          prediction, setPrediction, h2h, setH2h, openDetails, cache, cacheSet } = props;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [activeSlot, setActiveSlot] = useState(null);   // "home" | "away" | null
  const [lineup, setLineup] = useState(null);
  const [lineupBusy, setLineupBusy] = useState(false);
  const fixtures = useCached(cache, cacheSet, "fixtures",
    () => api("/api/fixtures/upcoming", { days: 8, limit: 15 }).then((d) => d.fixtures || []));

  const autoNeutral = (a, b) =>
    setNeutral(!!(a && b && a.scope === "intl" && b.scope === "intl"));

  // crest, colours and today's effective rating arrive after the pick; each
  // merge is guarded so a late response never overwrites a newer selection
  const hydrate = (t, set) => {
    for (const path of ["/api/logo", "/api/teamstate"]) {
      api(path, { team_id: t.id })
        .then((info) => set((cur) => (cur && cur.id === t.id ? { ...cur, ...info } : cur)))
        .catch(() => {});
    }
  };

  const loadPair = (ht, at) => {
    springy();
    setHome(ht); setAway(at);
    setNeutral(false);              // a listed fixture is a real home game
    setActiveSlot(null);
    hydrate(ht, setHome); hydrate(at, setAway);
  };

  const pickTeam = (slot, t) => {
    const set = slot === "home" ? setHome : setAway;
    set(t);
    autoNeutral(slot === "home" ? t : home, slot === "home" ? away : t);
    setActiveSlot(slot === "home" && !away ? "away" : null);
    springy();
    hydrate(t, set);
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
          <GradientBG id="welcome" from={C.gradFrom} to={C.gradTo} rounded={20}
            style={{ left: -18, right: -18, top: -18, bottom: -18 }} />
          <View style={{ flex: 1, paddingRight: 10 }}>
            {!!meta?.season && <Text style={s.bannerKicker}>SEASON {meta.season}</Text>}
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
        <GradientBG id="cta" from={C.gradFrom} to={C.gradTo} rounded={16} />
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
          <FixturesRail title="Kicking off soon" note="real schedule, next 8 days" rows={fixtures}
            renderCard={(f) => <FootballFixtureCard key={`${f.home_id}:${f.away_id}`} f={f}
              onPress={() => loadPair(fixtureTeam(f, "home"), fixtureTeam(f, "away"))} />} />
          <MarqueeRow rows={fixtures} onLoad={loadPair} />
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
              ) : <GradientBG id="hero" from={C.heroFrom} to={C.heroTo} rounded={18}
                    style={{ left: -20, right: -20, top: -20, bottom: -20 }} />;
            })()}
            <View style={s.heroRow}>
              <View style={s.heroCol}>
                {home?.badge ? <Image source={{ uri: home.badge }} style={s.heroBadge} />
                  : <View style={[s.heroBadge, s.heroBadgePh]}><Feather name="shield" size={22} color={HERO.dim} /></View>}
                <Text style={s.heroName} numberOfLines={2} adjustsFontSizeToFit>{p.home.name}</Text>
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
                <Text style={s.heroName} numberOfLines={2} adjustsFontSizeToFit>{p.away.name}</Text>
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

          {/* -------- how the game opens: first goal + half-time -------- */}
          {p.markets.first_goal && p.markets.half_time && (
            <Card delay={20}>
              <SectionTitle icon="play-circle">How the game opens</SectionTitle>
              {[
                [`${p.home.name} scores first`, p.markets.first_goal.home, C.lime],
                [`${p.away.name} scores first`, p.markets.first_goal.away, C.sky],
                ["Neither (0-0)", p.markets.first_goal.none, C.muted],
              ].map(([label, prob, col]) => <MarketRow key={label} label={label} prob={prob} color={col} />)}
              <View style={{ height: 10 }} />
              {[
                [`${p.home.name} ahead at half-time`, p.markets.half_time.home, C.lime],
                ["Level at half-time", p.markets.half_time.draw, C.muted],
                [`${p.away.name} ahead at half-time`, p.markets.half_time.away, C.sky],
              ].map(([label, prob, col]) => <MarketRow key={label} label={label} prob={prob} color={col} />)}
              <Text style={s.axisNote}>
                Likeliest half-time score {p.markets.half_time.score} ({(p.markets.half_time.score_prob * 100).toFixed(0)}%),
                and a {(p.markets.half_time.over_05 * 100).toFixed(0)}% chance of a goal before the break.
                {p.model_detail?.first_half_share != null
                  ? ` First halves carry about ${Math.round(p.model_detail.first_half_share * 100)}% of the goals, so half-time leads run smaller than full-time ones.`
                  : ""}
              </Text>
            </Card>
          )}

          {/* -------- probable line-ups on the pitch -------- */}
          <Card delay={60}>
            <SectionTitle icon="users">Probable line-ups</SectionTitle>
            {lineup ? <TiltIn><PitchXI data={lineup} colors={{ home: kitH, away: kitA }} /></TiltIn>
              : lineupBusy ? <BallLoader label="Warming up: pulling squads and player photos…" />
              : <Text style={s.dimTxt}>No public squad data for this pairing yet.</Text>}
          </Card>

          {/* -------- winning margin + scoreline heatmap -------- */}
          <Card delay={70}>
            <SectionTitle icon="grid" note="how far apart they finish">Winning margin</SectionTitle>
            {p.markets.margins ? (() => {
              const mg = p.markets.margins;
              const rows = [
                [`${p.home.name} by 2+`, mg.home_by_2_plus, C.lime],
                [`${p.home.name} by 1`, mg.home_by_1, C.lime],
                ["Level", mg.draw, C.muted],
                [`${p.away.name} by 1`, mg.away_by_1, C.sky],
                [`${p.away.name} by 2+`, mg.away_by_2_plus, C.sky],
              ];
              const max = Math.max(...rows.map((r) => r[1]), 0.01);
              return (
                <View>
                  {rows.map(([label, prob, col]) => <MarketRow key={label} label={label} prob={prob} color={col} max={max} />)}
                  {!!p.margin_note && <Text style={[s.dimTxt, { marginTop: 8 }]}>{p.margin_note}</Text>}
                </View>
              );
            })() : null}
            <View style={{ marginTop: 12 }}>
              <Heatmap matrix={p.score_matrix} homeName={p.home.name} awayName={p.away.name} />
            </View>
            <Text style={s.axisNote}>
              likeliest single score {p.markets.correct_scores[0].score} ({(p.markets.correct_scores[0].prob * 100).toFixed(1)}%)
            </Text>
          </Card>

          {/* -------- markets -------- */}
          <Card delay={140}>
            <SectionTitle icon="list">Every market, our fair price</SectionTitle>
            {(() => { const mk = p.markets, t25 = mk.totals["2.5"]; return [
              ["Over 2.5 goals", t25.over, t25.fair_over],
              ["Under 2.5 goals", t25.under, t25.fair_under],
              ["Both teams score", mk.btts.yes, mk.btts.fair_yes],
              [`${p.home.name} or draw`, mk.double_chance["1X"], mk.double_chance.fair_odds?.["1X"] ?? null],
              [`${p.home.name} clean sheet`, mk.clean_sheet.home, mk.clean_sheet.fair_odds?.home ?? null],
              [`${p.away.name} clean sheet`, mk.clean_sheet.away, mk.clean_sheet.fair_odds?.away ?? null],
            ]; })().map(([label, prob, fair]) =>
              <MarketRow key={label} label={label} prob={prob} color={C.sky} fair={fair} height={5} />)}
            <Text style={s.axisNote}>bet any of these only when a book offers MORE than the fair price</Text>
          </Card>

          {/* -------- scorers and assisters -------- */}
          {Object.entries(p.likely_scorers || {}).map(([team, players]) => {
            const assisters = p.likely_assisters?.[team] || [];
            if (players.length === 0 && assisters.length === 0) return null;
            return (
              <Card key={team}>
                {players.length > 0 && (
                  <>
                    <SectionTitle icon="target">Chance to score · {team}</SectionTitle>
                    {players.slice(0, 5).map((x) => <PlayerProbRow key={x.player} name={x.player} prob={x.prob_to_score} />)}
                  </>
                )}
                {assisters.length > 0 && (
                  <>
                    <SectionTitle icon="share-2">Likely to assist · {team}</SectionTitle>
                    {assisters.slice(0, 5).map((x) => <PlayerProbRow key={x.player} name={x.player} prob={x.prob_to_assist} />)}
                  </>
                )}
              </Card>
            );
          })}

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
          <FixturesRail title="Kicking off soon" note="real schedule, next 8 days" rows={fixtures}
            renderCard={(f) => <FootballFixtureCard key={`${f.home_id}:${f.away_id}`} f={f}
              onPress={() => { const ht = fixtureTeam(f, "home"), at = fixtureTeam(f, "away");
                loadPair(ht, at); run(ht, at, false); }} />} />
        </>
      )}
    </ScrollView>
  );
}

/* one player line: name, bar, % (scorers and assisters share it) */
function PlayerProbRow({ name, prob }) {
  return (
    <View style={s.scorerRow}>
      <Text style={s.scorerName} numberOfLines={1}>{name}</Text>
      <View style={{ flex: 1 }}><HBar frac={prob} color={C.lime} /></View>
      <Text style={[s.scorerP, TNUM, { color: rateColor(prob, 0.25, 0.12), fontWeight: "700" }]}>
        {(prob * 100).toFixed(0)}%</Text>
    </View>
  );
}

/* ================= basketball: NBA predict screen ================= */

function NbaFixtureCard({ f, onPress }) {
  const when = fmtKickoff(f.kickoff);
  return (
    <PressScale style={s.mqCard} onPress={onPress}
      accessibilityLabel={`${f.away_team?.name} at ${f.home_team?.name}, ${when}`}>
      <Text style={[s.mqTag, TNUM, { color: C.muted, letterSpacing: 1.2 }]} numberOfLines={1}>
        {f.live ? <Text style={{ color: C.lime }}>LIVE · </Text> : null}{when}</Text>
      {[[f.away_team, "AWAY"], [f.home_team, "HOME"]].map(([t, tag]) => (
        <View key={tag} style={[s.rowBetween, { gap: 8, marginTop: tag === "AWAY" ? 8 : 2 }]}>
          <Text style={[s.optName, { fontSize: 14, flexShrink: 1 }]} numberOfLines={1}>{t?.name}</Text>
          <Text style={s.lineHd}>{f.neutral ? "NEUTRAL" : tag}</Text>
        </View>))}
      <Text style={[s.mqNames, NUM, TNUM, { fontSize: 13, color: C.dim }]} numberOfLines={1}>
        {f.book_line || "line tba"}{f.book_total ? `  ·  O/U ${f.book_total}` : ""}</Text>
    </PressScale>
  );
}

function NbaSlot({ slot, team, active, onPress }) {
  return (
    <PressScale style={[s.slotCard, active && s.slotCardOn]} containerStyle={{ flex: 1 }}
      onPress={onPress} accessibilityLabel={`${slot} team`}>
      <View style={[s.slotSideTag, s.slotSideTagNba]}><Text style={s.slotSideTxt}>{slot.toUpperCase()}</Text></View>
      {team?.logo ? <Image source={{ uri: team.logo }} style={s.slotBadge} />
        : <View style={s.slotBadgePh}><Feather name="plus" size={20} color={C.muted} /></View>}
      <Text style={[s.slotName, { fontSize: 14, lineHeight: 18 }]} numberOfLines={2} adjustsFontSizeToFit>
        {team ? team.name : `Pick ${slot}`}</Text>
      <Text style={[s.slotSub, TNUM]}>{team ? `Elo ${team.elo}` : "tap to search"}</Text>
    </PressScale>
  );
}

// one scoreboard line: logo, name and side, win chance, projected points
function NbaTeamLine({ p, side, team, color, tag }) {
  const t = p[side];
  const fav = p.win_prob[side] >= p.win_prob[side === "home" ? "away" : "home"];
  return (
    <View style={s.sbRow}>
      {team?.logo ? <Image source={{ uri: team.logo }} style={{ width: 36, height: 36 }} />
        : <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: C.panel2 }} />}
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={s.sbName} numberOfLines={2} adjustsFontSizeToFit>{t.name}</Text>
        <Text style={s.sbSide}>{p.neutral_venue ? "NEUTRAL" : tag}
          <Text style={[TNUM, { color: C.dim, letterSpacing: 0 }]}>  · Elo {t.elo}</Text></Text>
      </View>
      <View style={{ alignItems: "flex-end" }}>
        <Text style={[s.sbPts, NUM, TNUM, fav && { color: C.lime }]}>{p.projected[side].toFixed(0)}</Text>
        <View style={{ flexDirection: "row", alignItems: "baseline", gap: 6 }}>
          <CountUp value={p.win_prob[side] * 100} decimals={1} suffix="%" style={[s.sbPct, NUM, { color }]} />
          <Text style={[s.sbFair, TNUM]}>fair {fmtOdds(p.win_prob.fair_odds[side])}</Text>
        </View>
      </View>
    </View>
  );
}

function NbaCell({ value, label, on, last }) {
  return (
    <View style={[s.sbCell, last && { borderRightWidth: 0 }]}>
      <Text style={[s.sbCellVal, NUM, TNUM, on && { color: C.lime }]}>{value}</Text>
      <Text style={s.sbCellLbl}>{label}</Text>
    </View>
  );
}

// a two-sided line: the line, left-cover bar + %, right-cover bar + %
function NbaLineRow({ line, left, right, lc, rc }) {
  return (
    <View style={s.lineRow}>
      <Text style={[s.lineNum, NUM, TNUM]}>{line}</Text>
      <View style={{ flex: 1 }}><HBar frac={left} color={lc} height={6} /></View>
      <Text style={[s.linePct, NUM, TNUM]}>{(left * 100).toFixed(0)}%</Text>
      <View style={{ flex: 1 }}><HBar frac={right} color={rc} height={6} /></View>
      <Text style={[s.linePct, NUM, TNUM]}>{(right * 100).toFixed(0)}%</Text>
    </View>
  );
}
function NbaLineHd({ l, r }) {
  return (
    <View style={[s.lineRow, { borderBottomWidth: 0, paddingVertical: 2 }]}>
      <Text style={[s.lineHd, { width: 54 }]}>LINE</Text>
      <Text style={[s.lineHd, { flex: 1 }]} numberOfLines={1}>{l}</Text><View style={{ width: 44 }} />
      <Text style={[s.lineHd, { flex: 1 }]} numberOfLines={1}>{r}</Text><View style={{ width: 44 }} />
    </View>
  );
}

const restCopy = (t) => t.rest_days == null ? "rest unknown"
  : t.rest_days <= 1 ? "back-to-back, second night"
  : t.rest_days > 10 ? `${t.rest_days} days since their last game` : `${t.rest_days} days' rest`;

function NbaInjuries({ t }) {
  const inj = t.injuries || [];
  return (
    <View>
      <View style={s.sbTeam}>
        <Text style={s.sbTeamName}>{t.name.toUpperCase()}</Text>
        <Text style={s.sbTeamNote} numberOfLines={2}>{restCopy(t)}</Text>
      </View>
      {t.injuries_unavailable ? <Text style={s.axisNote}>Injury report unavailable right now.</Text>
        : inj.length === 0 ? <Text style={s.axisNote}>No one on the injury report.</Text>
        : inj.map((i, k) => (
          <View key={k} style={[s.rowBetween, { paddingVertical: 6, borderTopWidth: k ? 1 : 0, borderTopColor: C.line, gap: 10 }]}>
            <Text style={[s.dimTxt, { fontSize: 13.5, lineHeight: 18, color: C.chalk, flexShrink: 1 }]} numberOfLines={1}>
              {i.player}{i.ppg ? <Text style={{ color: C.muted }}>  {i.ppg} ppg</Text> : null}</Text>
            <Text style={[s.smallLabel, { color: C.muted, fontSize: 12.5 }, /out/i.test(i.status) && { color: C.red, fontWeight: "700" }]}
              numberOfLines={1}>{i.status}{i.detail ? ` · ${i.detail}` : ""}</Text>
          </View>))}
    </View>
  );
}

/* ================= basketball: player props ================= */

const PROP_COLS = [["points", "PTS"], ["rebounds", "REB"], ["assists", "AST"], ["threes", "3PM"]];

// one team's rotation: a header row, then per player the line and the over chance
function PropsTeam({ team, color }) {
  const players = team?.players || [];
  if (players.length === 0) return null;
  return (
    <View style={{ marginTop: 6 }}>
      <View style={[s.propRow, { borderTopWidth: 0, paddingBottom: 2 }]}>
        <Text style={[s.sbTeamName, { flex: 1 }]} numberOfLines={1}>{String(team.name || "").toUpperCase()}</Text>
        {PROP_COLS.map(([, lbl]) => <Text key={lbl} style={[s.lineHd, s.propCell, { textAlign: "center" }]}>{lbl}</Text>)}
      </View>
      {players.map((pl) => {
        const flag = /quest|doubt/i.test(pl.status || "");
        return (
          <View key={pl.name} style={s.propRow}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.propName} numberOfLines={1}>{pl.name}
                {flag ? <Text style={{ color: C.amber, fontSize: 12 }}>  {pl.status}</Text> : null}</Text>
              <Text style={[s.optSub, TNUM, { marginTop: 1 }]} numberOfLines={1}>
                {pl.pos}{pl.minutes != null ? ` · ${Math.round(pl.minutes)} min` : ""}</Text>
            </View>
            {PROP_COLS.map(([key, lbl]) => {
              const st = pl.stats?.[key];
              const over = st?.over;
              return (
                <View key={lbl} style={s.propCell}>
                  <Text style={[s.propLine, NUM, TNUM]}>{st?.line ?? "–"}</Text>
                  <Text style={[s.propOver, NUM, TNUM, over != null && over >= 0.5 && { color }]}>
                    {over != null ? `${Math.round(over * 100)}%` : "–"}</Text>
                </View>
              );
            })}
          </View>
        );
      })}
    </View>
  );
}

function NbaPropsCard({ api, nba, patchNba, kitH, kitA, bankroll }) {
  const [busy, setBusy] = useState(false);
  const d = nba.props, mk = nba.propsMarket;
  const compare = async () => {
    setBusy(true);
    try {
      patchNba({ propsMarket: await api("/api/nba/props/market", { home: nba.home.id, away: nba.away.id }) });
    } catch (e) {
      patchNba({ propsMarket: { error: "network", errors: [String(e.message)], bets: [] } });
    }
    setBusy(false);
  };
  return (
    <Card delay={210}>
      <SectionTitle icon="user" note="line · chance of over">Player props</SectionTitle>
      {!d ? <BallLoader label="Pulling each rotation's season numbers…" />
        : d.error ? <Text style={s.dimTxt}>Player props didn't load: {d.error}.</Text>
        : (
          <>
            <PropsTeam team={d.home} color={kitH} />
            <PropsTeam team={d.away} color={kitA} />
            {!!d.note && <Text style={[s.axisNote, { marginTop: 12 }]}>{d.note}</Text>}
            {(d.caveats || []).map((c, i) => (
              <View key={i} style={s.caveatRow}>
                <Feather name="alert-triangle" size={13} color={C.amber} /><Text style={s.caveat}> {c}</Text>
              </View>
            ))}
            {!mk && !busy && (
              <Pressable style={({ pressed }) => [s.detailsBtn, pressed && { opacity: 0.8 }]} onPress={compare}>
                <Feather name="bar-chart-2" size={14} color={C.lime} />
                <Text style={s.detailsBtnTxt}>  Compare with the books (4 odds credits)</Text>
              </Pressable>
            )}
            {busy && <BallLoader label="Shopping live US sportsbooks for prop prices…" />}
            {mk && !busy && (
              <BetResults data={mk} bankroll={bankroll} showMatch={false}
                empty={<Text style={[s.dimTxt, { marginTop: 12 }]}>The books have no prop prices for this game yet.</Text>} />
            )}
          </>
        )}
    </Card>
  );
}

function NbaPredictScreen({ api, nba, patchNba, nbaMeta, cache, cacheSet, bankroll }) {
  const { home, away, neutral, date, prediction: p } = nba;
  const [active, setActive] = useState(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const runId = useRef(0);
  const teams = useCached(cache, cacheSet, "nbaTeams", () => api("/api/nba/teams"));
  const fixtures = useCached(cache, cacheSet, "nbaFixtures",
    () => api("/api/nba/fixtures", { days: 7 }).then((d) => d.fixtures || []));
  const teamList = Array.isArray(teams) ? teams : [];

  const pick = (slot, t) => {
    springy(); patchNba({ [slot]: t, date: null });
    setActive(slot === "home" && !away ? "away" : null); setQ("");
  };
  // `when` is the fixture kickoff (ISO) when the game came off the rail, so the
  // server prices that night's rest and injury report rather than today's
  const run = async (h = home, a = away, n = neutral, when = date) => {
    if (!h || !a) return;
    const id = ++runId.current;
    setBusy(true); setErr(""); setActive(null);
    const dateQ = when ? { date: when } : {};
    patchNba({ props: null, propsMarket: null });
    api("/api/nba/props", { home: h.id, away: a.id, ...dateQ })
      .then((d) => { if (runId.current === id) patchNba({ props: d }); })
      .catch((e) => { if (runId.current === id) patchNba({ props: { error: String(e.message) } }); });
    try {
      const d = await api("/api/nba/predict", { home: h.id, away: a.id, neutral: n, ...dateQ });
      if (runId.current !== id) return;
      patchNba({ prediction: d });
    } catch (e) { if (runId.current === id) setErr(String(e.message)); }
    if (runId.current === id) setBusy(false);
  };
  const loadPair = (f) => {
    const h = f.home_team, a = f.away_team;
    if (!h?.id || !a?.id) return;
    springy();
    patchNba({ home: h, away: a, neutral: !!f.neutral, date: f.kickoff || null });
    run(h, a, !!f.neutral, f.kickoff || null);
  };
  const swap = () => {
    if (!home || !away) return;
    springy(); patchNba({ home: away, away: home });
    if (p) run(away, home, neutral);
  };
  const opts = teamList.filter((t) => q.trim().length >= 1
    && (t.name.toLowerCase().includes(q.toLowerCase()) || (t.abbr || "").toLowerCase() === q.toLowerCase())).slice(0, 6);
  const kitH = kitColor(home?.color, true) || C.chalk;
  let kitA = kitColor(away?.color, true) || C.sky;
  if (colorDist(kitH, kitA) < 95) kitA = kitColor(away?.alt, true) || C.sky;
  const sgn = (v) => `${v > 0 ? "+" : ""}${v || 0}`;
  const nSeasons = nbaMeta?.fit?.seasons?.length;
  const nGames = nbaMeta?.fit?.games ?? nbaMeta?.games;
  const historyCopy = nSeasons
    ? `${nSeasons} seasons of results${nGames ? ` (${Number(nGames).toLocaleString()} games)` : ""}`
    : "past seasons of results";

  return (
    <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">
      {!p && !busy && (
        <View style={s.nbBanner}>
          <View style={{ flex: 1, paddingRight: 12 }}>
            {!!nbaMeta?.season && <Text style={s.nbKicker}>NBA · {nbaMeta.season} SEASON</Text>}
            <Text style={[s.nbTitle, NUM]}>CALL ANY NBA GAME</Text>
            <Text style={s.nbSub}>Win probability, projected score, spread and total from {historyCopy}, tonight's injury report and each side's rest.</Text>
          </View>
          <Ionicons name="basketball-outline" size={58} color={C.lime} />
        </View>
      )}
      <View style={s.slotRow}>
        <NbaSlot slot="home" team={home} active={active === "home"}
          onPress={() => { springy(); setActive(active === "home" ? null : "home"); }} />
        <View style={s.vsWrap}>
          <Text style={s.vsTxt}>VS</Text>
          <Pressable disabled={!home || !away} onPress={swap} hitSlop={10} accessibilityLabel="Switch home and away"
            style={[s.swapBtn, (!home || !away) && { opacity: 0.35 }]}><Feather name="repeat" size={15} color={C.dim} /></Pressable>
        </View>
        <NbaSlot slot="away" team={away} active={active === "away"}
          onPress={() => { springy(); setActive(active === "away" ? null : "away"); }} />
      </View>
      {active && (
        <View style={{ marginTop: 10 }}>
          <TextInput style={s.input} value={q} onChangeText={setQ} autoFocus placeholder={`Search the ${active} team…`}
            placeholderTextColor={C.muted} autoCorrect={false} keyboardAppearance="dark" />
          {opts.length > 0 && (
            <View style={s.dropdown}>
              {opts.map((t) => (
                <Pressable key={t.id} style={({ pressed }) => [s.opt, pressed && { backgroundColor: C.panel2 }]} onPress={() => pick(active, t)}>
                  <View style={[s.row, { gap: 10 }]}>
                    {t.logo ? <Image source={{ uri: t.logo }} style={{ width: 26, height: 26 }} /> : null}
                    <View><Text style={s.optName}>{t.name}</Text><Text style={[s.optSub, TNUM]}>Elo {t.elo}</Text></View>
                  </View>
                </Pressable>
              ))}
            </View>
          )}
          {teamList.length > 0 && q.trim().length >= 2 && opts.length === 0 && <Text style={[s.axisNote, { marginTop: 8 }]}>No team matches "{q}".</Text>}
          {!!teams?.error && <Text style={s.errTxt}>{teams.error}</Text>}
        </View>
      )}
      <PressScale disabled={!home || !away || busy} onPress={() => run()} style={[s.cta, s.ctaNba, (!home || !away || busy) && s.btnOff]}>
        <Feather name="zap" size={17} color={C.onAccent} />
        <Text style={s.ctaTxt}>{busy ? "Computing…" : "Predict this game"}</Text>
      </PressScale>
      <View style={[s.row, { marginTop: 14, justifyContent: "center" }]}>
        <Switch value={neutral} onValueChange={(v) => patchNba({ neutral: v })}
          trackColor={{ true: C.lime, false: C.panel2 }} thumbColor={C.chalk} />
        <Text style={s.dimTxt}>  neutral court</Text>
      </View>
      {!!err && <Text style={s.errTxt}>{err}. If this keeps happening, check the server address in Settings.</Text>}
      {busy && <BallLoader label={nSeasons ? `Replaying ${nSeasons} seasons of NBA results…` : "Replaying the NBA results history…"} />}
      {!p && !busy && <FixturesRail title="Tipping off soon" note="official schedule, next 7 days" rows={fixtures}
        renderCard={(f, i) => <NbaFixtureCard key={i} f={f} onPress={() => loadPair(f)} />} />}

      {p && !busy && (() => {
        const w = p.win_prob, pr = p.projected, inp = p.inputs, mg = p.margins;
        const mrows = [[`${p.home.name} by 11+`, mg.home_by_11_plus, kitH], [`${p.home.name} by 6-10`, mg.home_by_6_10, kitH], [`${p.home.name} by 1-5`, mg.home_by_1_5, kitH],
          [`${p.away.name} by 1-5`, mg.away_by_1_5, kitA], [`${p.away.name} by 6-10`, mg.away_by_6_10, kitA], [`${p.away.name} by 11+`, mg.away_by_11_plus, kitA]];
        const mmax = Math.max(...mrows.map((r) => r[1]), 0.01);
        const under = "rgba(255,255,255,0.28)";
        const ab = (key) => ((key === "home" ? home : away)?.abbr) || p[key].name.toUpperCase();
        const favKey = pr.margin >= 0 ? "home" : "away";
        const spreadTxt = Math.abs(pr.margin) < 0.05 ? "PICK 'EM" : `${ab(favKey)} -${Math.abs(pr.margin).toFixed(1)}`;
        const slope = inp.points_per_elo != null && inp.rating_pts != null;
        return (
          <>
            <Card delay={0} style={s.heroCard}>
              <View style={s.sbTop}>
                <Text style={[s.sbTopTxt, { color: C.lime, flexShrink: 0 }]} numberOfLines={1}>PROJECTED FINAL{p.neutral_venue ? <Text style={{ color: C.muted }}> · NEUTRAL</Text> : null}</Text>
                <Text style={[s.sbTopTxt, TNUM, { color: C.dim, flex: 1, textAlign: "right" }]} numberOfLines={1} adjustsFontSizeToFit>{spreadTxt} · O/U {pr.total.toFixed(1)}</Text>
              </View>
              <NbaTeamLine p={p} side="home" team={home} color={kitH} tag="HOME" />
              <NbaTeamLine p={p} side="away" team={away} color={kitA} tag="AWAY" />
              <View style={{ marginTop: 14 }}>
                <StackBar segs={[{ frac: w.home, color: kitH }, { frac: w.away, color: kitA }]} height={6} />
                <View style={s.sbCap}>
                  <Text style={[s.sbCapTxt, TNUM, { color: kitH }]}>{ab("home")} {(w.home * 100).toFixed(0)}%</Text>
                  <Text style={s.sbCapTxt}>WIN PROBABILITY</Text>
                  <Text style={[s.sbCapTxt, TNUM, { color: kitA }]}>{(w.away * 100).toFixed(0)}% {ab("away")}</Text>
                </View>
              </View>
              <View style={s.sbCells}>
                <NbaCell value={inp.home_court_pts > 0 ? `+${inp.home_court_pts}` : "0"} label="home court, pts" on={!!inp.home_court_pts} />
                <NbaCell value={sgn(inp.rest_adjust)} label="rest edge, pts" on={!!inp.rest_adjust} />
                <NbaCell value={sgn(inp.injury_adjust)} label="injury edge, pts" on={!!inp.injury_adjust} last />
              </View>
              <Text style={s.sbNote}>{p.margin_note}</Text>
            </Card>
            <Card delay={70}>
              <SectionTitle icon="grid" note="how far apart they finish">Winning margin</SectionTitle>
              {mrows.map(([l, v, c]) => <MarketRow key={l} label={l} prob={v} color={c} max={mmax} />)}
              <Text style={s.axisNote}>each band is the chance the final margin lands there; bars scale to the likeliest band</Text>
            </Card>
            <Card delay={100}>
              <SectionTitle icon="bar-chart-2" note="chance each side covers">Spread lines</SectionTitle>
              <NbaLineHd l={p.home.name.toUpperCase()} r={p.away.name.toUpperCase()} />
              {p.spreads.map((x) => <NbaLineRow key={x.line} line={`${x.line > 0 ? "+" : ""}${x.line}`} left={x.home_cover} right={x.away_cover} lc={kitH} rc={kitA} />)}
              <Text style={s.axisNote}>lines are from {p.home.name}'s side: a negative number means they are giving points</Text>
            </Card>
            <Card delay={130}>
              <SectionTitle icon="list" note="over / under">Total points</SectionTitle>
              <NbaLineHd l="OVER" r="UNDER" />
              {p.totals.map((x) => <NbaLineRow key={x.line} line={`${x.line}`} left={x.over} right={x.under} lc={C.lime} rc={under} />)}
              <Text style={s.axisNote}>projected total {pr.total.toFixed(1)}; game-to-game totals scatter by about {inp.sd_total} points</Text>
            </Card>
            <Card delay={160}>
              <SectionTitle icon="clock" note={`projected ${p.half_time.proj_home}–${p.half_time.proj_away}`}>At half-time</SectionTitle>
              <MarketRow label={`${p.home.name} ahead`} prob={p.half_time.home} color={kitH} />
              <MarketRow label={`${p.away.name} ahead`} prob={p.half_time.away} color={kitA} />
              {inp.half_share != null && (
                <Text style={s.axisNote}>first halves carry about {Math.round(inp.half_share * 100)}% of the points, so half-time leads run smaller than final margins</Text>
              )}
            </Card>
            <Card delay={190}>
              <SectionTitle icon="users">Injuries and rest</SectionTitle>
              <NbaInjuries t={p.home} /><NbaInjuries t={p.away} />
            </Card>
            <NbaPropsCard api={api} nba={nba} patchNba={patchNba} kitH={kitH} kitA={kitA} bankroll={bankroll} />
            {(p.h2h || []).length > 0 && (
              <Card delay={220}>
                <SectionTitle icon="repeat" note="most recent first">Head to head</SectionTitle>
                {p.h2h.map((g, i) => (
                  <View key={i} style={s.meetRow}>
                    <Text style={[s.meetDate, TNUM]}>{g.date.slice(0, 7)}</Text>
                    <Text style={s.meetMatch} numberOfLines={1}>{g.away} @ {g.home}</Text>
                    <Text style={[s.meetScore, NUM, TNUM, { width: 82, fontSize: 15 }]}>{g.score}</Text>
                  </View>))}
                <SectionTitle icon="trending-up">Strength over time (Elo)</SectionTitle>
                <EloLineChart histHome={p.elo_history.home} histAway={p.elo_history.away} nameHome={p.home.name} nameAway={p.away.name} />
              </Card>
            )}
            <Card delay={250}>
              <SectionTitle icon="divide">The math, with this game's numbers</SectionTitle>
              <Text style={s.dimTxt}>Rating gap {inp.elo_gap > 0 ? "+" : ""}{inp.elo_gap}{slope ? ` (one point of margin per ${Math.round(inp.rating_pts)} rating points) is worth ${(inp.elo_gap * inp.points_per_elo).toFixed(1)} points` : ""}, plus home court {inp.home_court_pts}{inp.rest_adjust ? `, rest ${inp.rest_adjust}` : ""}{inp.injury_adjust ? `, injuries ${inp.injury_adjust}` : ""}: projected margin {pr.margin > 0 ? "+" : ""}{pr.margin}. Actual margins scatter around that by about {inp.sd_margin} points, so the share of the curve above zero is the win chance, and every spread line is the same curve cut elsewhere. The total comes from each side's recent scoring against the other's recent defending.</Text>
            </Card>
            {(p.caveats || []).map((c, i) => (
              <View key={i} style={s.caveatRow}><Feather name="alert-triangle" size={13} color={C.amber} /><Text style={s.caveat}> {c}</Text></View>
            ))}
            <FixturesRail title="Tipping off soon" note="official schedule, next 7 days" rows={fixtures}
              renderCard={(f, i) => <NbaFixtureCard key={i} f={f} onPress={() => loadPair(f)} />} />
          </>
        );
      })()}
    </ScrollView>
  );
}

/* ================= bet cards: Vs Market and player props share them ================= */

const ODDS_ERR = {
  quota: "The odds allowance for this month is used up; it resets on the 1st.",
  invalid_key: "The server has no working Odds API key configured, so live prices can't be fetched.",
  no_key: "The server has no working Odds API key configured, so live prices can't be fetched.",
  network: "The odds feed didn't answer. Try again in a minute.",
};

function OddsErrorCard({ data }) {
  return (
    <Card>
      <SectionTitle icon="alert-circle">The odds feed isn't available</SectionTitle>
      <Text style={s.dimTxt}>{ODDS_ERR[data.error] || data.detail || "The odds feed returned an error."}</Text>
      {(data.errors || []).slice(0, 3).map((e, i) => (
        <Text key={i} style={[s.axisNote, { marginTop: 6 }]}>{String(e)}</Text>
      ))}
    </Card>
  );
}

function BetCard({ b, bankroll, showMatch }) {
  const good = !!b.value;
  return (
    <Card style={good ? s.cardGood : null}>
      <View style={s.rowBetween}>
        <Text style={s.betOutcome}>{b.outcome}</Text>
        {good ? (
          <View style={s.edgeChip}>
            <Feather name="trending-up" size={12} color={C.onAccent} />
            <Text style={[s.edgeChipTxt, TNUM]}> +{b.edge_pct}%</Text>
          </View>
        ) : <Text style={s.noValTxt}>no value</Text>}
      </View>
      {showMatch && !!b.match && <Text style={s.dimTxt}>{b.match}</Text>}
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
}

// Everything under a scan: the error card when the feed failed, the rows, or
// the caller's empty note; then books scanned and credits left.
function BetResults({ data, bankroll, showMatch, empty }) {
  const rows = data.selected?.bets || data.bets || [];
  const failed = !!data.error || ((data.errors || []).length > 0 && rows.length === 0);
  return (
    <>
      {failed ? <OddsErrorCard data={data} />
        : rows.length === 0 ? empty
        : rows.map((b, i) => <BetCard key={i} b={b} bankroll={bankroll} showMatch={showMatch} />)}
      {!failed && (data.n_books != null || data.remaining_credits != null) && (
        <Text style={[s.axisNote, { marginTop: 10 }]}>
          {[data.n_books != null && `${data.n_books} books scanned`,
            data.remaining_credits != null && `odds credits left: ${data.remaining_credits}`,
            "edges are long-run advantages, not sure things"].filter(Boolean).join(" · ")}
        </Text>
      )}
    </>
  );
}

/* ================= vs market ================= */
function BestBetsScreen({ api, home, away, bankroll, sport, cache, cacheSet }) {
  const nba = sport === "basketball";
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // a scan spends odds credits, so the last result is kept until the next explicit scan
  const key = `bets:${sport}:${!nba && home && away ? `${home.id}:${away.id}` : "all"}`;
  const data = cache[key];

  const run = async () => {
    setBusy(true); setErr(""); cacheSet(key, undefined);
    try {
      const params = !nba && home && away ? { home: home.id, away: away.id } : undefined;
      cacheSet(key, await api(nba ? "/api/nba/bestbets" : "/api/bestbets", params));
    } catch (e) { setErr(String(e.message)); }
    setBusy(false);
  };

  const emptyCopy = (d) => (!nba && home && !d.selected)
    ? (d.fixtures === 1
        ? "The books list this match, but none of its markets can be graded right now."
        : "The sportsbooks aren't listing this match yet. Books usually price games one or two days before kickoff, so check back then.")
    : d.fixtures === 0
      ? (nba ? "The sportsbooks aren't listing NBA games in the next two days yet. Lines usually appear the day before tip-off."
          : "The sportsbooks aren't listing any football in the next two days. This happens between rounds and in the offseason. Check back on a match week.")
      : `Checked ${d.fixtures} listed game${d.fixtures === 1 ? "" : "s"}. No price beats the combined estimate right now, which is the normal state. Not betting today costs you nothing.`;

  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>Bookmakers vs our model</Text>
      <Text style={s.dimTxt}>
        {`This page answers one question: is any sportsbook price actually worth taking? It pulls prices from live US sportsbooks, strips out their built-in margin to reveal the probability THEY believe, and puts it next to OURS. For every bet you get two bars: blue is the bookmakers' probability, ${nba ? "orange" : "green"} is our model's. When the best available price pays more than the combined estimate justifies, the card lights up ${nba ? "orange" : "green"} with a stake suggestion. No ${nba ? "orange" : "green"} card means no good bet exists, and skipping is the right move.`}
      </Text>
      <Pressable style={({ pressed }) => [s.btn, busy && s.btnOff,
        { marginTop: 14, alignItems: "center" }, pressed && { opacity: 0.8 }]}
        disabled={busy} onPress={run}>
        <Text style={s.btnTxt}>{busy ? "Checking prices…" : nba ? "Scan NBA games, next two days" : home && away ? `Grade ${home.name} v ${away.name}` : "Scan the next two days"}</Text>
      </Pressable>
      {!!err && <Text style={s.errTxt}>{err}</Text>}
      {busy && <BallLoader label="Shopping live US sportsbooks for prices…" />}

      {data && !busy && (
        <>
          {data.selected && <Text style={s.h3}>{data.selected.match}</Text>}
          <BetResults data={data} bankroll={bankroll} showMatch={!data.selected}
            empty={(
              <Card>
                <SectionTitle icon="info">Nothing to grade right now</SectionTitle>
                <Text style={s.dimTxt}>{emptyCopy(data)}</Text>
                {data.skipped?.length > 0 && (
                  <Text style={[s.axisNote, { marginTop: 8 }]}>skipped: {data.skipped.slice(0, 3).join(", ")}</Text>
                )}
              </Card>
            )} />
        </>
      )}
    </ScrollView>
  );
}

/* ================= parlays ================= */
function ParlaysScreen({ api, home, away, neutral, cache, cacheSet }) {
  const key = home && away ? `parlay:${home.id}:${away.id}:${neutral}` : null;
  const data = useCached(cache, cacheSet, key,
    () => api("/api/parlay/suggest", { home: home.id, away: away.id, neutral }));
  const parlays = data?.parlays || [];

  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>Parlay builds</Text>
      {!home || !away ? (
        <Text style={s.dimTxt}>Pick a match on the Predict tab first.</Text>
      ) : data?.error ? (
        <Text style={s.errTxt}>{data.error}</Text>
      ) : !data ? (
        <BallLoader label="Simulating this match…" />
      ) : (
        <>
          <Text style={s.dimTxt}>
            True hit chance from {data.n_sims != null ? `${Number(data.n_sims).toLocaleString()} simulated matches` : "simulated matches"}.
            Build the combo in your sportsbook and bet ONLY if their payout beats the "take if" number.
            Apply any 4+ leg boost first.
          </Text>
          {parlays.length === 0 && (
            <Card>
              <Text style={s.dimTxt}>
                No parlay clears the bar for this match: every template combo lands under
                {data.min_prob != null ? ` a ${(data.min_prob * 100).toFixed(1)}%` : " the minimum"} real chance,
                which is lottery territory. Skipping is the play.
              </Text>
            </Card>
          )}
          {parlays.map((pl) => (
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

function ModelTeam({ api, cache, cacheSet }) {
  const [w, setW] = useState(0);
  const t = useCached(cache, cacheSet, "fplSquad", () => api("/api/fpl/squad"));
  if (t?.error) return <Text style={s.dimTxt}>{t.detail || t.error}</Text>;
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
    : "held, nothing cleared the bar, so the free transfer banks";
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
      <Text style={s.axisNote}>{t.note}{t.durable === false ? " Warning: durable storage is not connected on this server, so this team's history resets on restart." : ""}</Text>
    </View>
  );
}

/* ================= fantasy premier league ================= */
function PlayerRow({ p, rank, expanded, onToggle }) {
return (
  <Pressable onPress={() => onToggle(p.id)}>
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
        {Object.entries(p.breakdown || {}).map(([k, v]) => (
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
}

function FPLScreen({ api, fplId, cache, cacheSet }) {
  const [pos, setPos] = useState("MID");
  const [expanded, setExpanded] = useState(null);
  const toggleRow = (id) => { springy(); setExpanded((cur) => (cur === id ? null : id)); };
  const gw = useCached(cache, cacheSet, "fplGw", () => api("/api/fpl/gw"));
  const team = useCached(cache, cacheSet,
    fplId && gw && !gw.error ? `fplEntry:${fplId}:${gw.gameweek}` : null,
    () => api(`/api/fpl/entry/${fplId}`));
  const teamErr = team?.error ? (team.detail || team.error) : "";

  if (gw?.error) return <ScrollView contentContainerStyle={s.scroll}><Text style={s.errTxt}>{gw.detail || gw.error}</Text></ScrollView>;
  if (!gw) return <ScrollView contentContainerStyle={s.scroll}><BallLoader label="Loading this gameweek…" /></ScrollView>;

  const days = Math.max(0, Math.ceil((new Date(gw.deadline) - Date.now()) / 86400000));
  const captains = gw.players.slice(0, 5);
  const diffs = gw.players.filter((p) => p.owned_pct < 10).slice(0, 6);
  const byPos = gw.players.filter((p) => p.pos === pos).slice(0, 8);

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
        <TiltIn><ModelTeam api={api} cache={cache} cacheSet={cacheSet} /></TiltIn>
      </Card>

      <Card>
        <SectionTitle icon="star">Captain picks <Text style={s.secNote}>doubled points</Text></SectionTitle>
        {captains.map((p, i) => <PlayerRow expanded={expanded} onToggle={toggleRow} key={p.id} p={p} rank={i + 1} />)}
      </Card>

      <Card>
        <SectionTitle icon="users">Best by position</SectionTitle>
        <View style={s.segRow}>
          {["GK", "DEF", "MID", "FWD"].map((k) => (
            <Pressable key={k} onPress={() => setPos(k)} style={[s.segBtn, pos === k && s.segBtnOn]}
              accessibilityRole="button" accessibilityState={{ selected: pos === k }}>
              <Text style={[s.segTxt, pos === k && { color: C.onAccent, fontWeight: "700" }]}>{k}</Text>
            </Pressable>
          ))}
        </View>
        {byPos.map((p, i) => <PlayerRow expanded={expanded} onToggle={toggleRow} key={p.id} p={p} rank={i + 1} />)}
      </Card>

      <Card>
        <SectionTitle icon="eye-off" note="under 10% owned">Differentials</SectionTitle>
        <Text style={s.dimTxt}>Strong projected scores that most managers don't have. When they pay off, you climb.</Text>
        {diffs.map((p, i) => <PlayerRow expanded={expanded} onToggle={toggleRow} key={p.id} p={p} rank={i + 1} />)}
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
            {team.squad.slice(0, 15).map((p, i) => <PlayerRow expanded={expanded} onToggle={toggleRow} key={p.id} p={p} rank={i + 1} />)}
            {team.upgrade_ideas?.length > 0 && (
              <>
                <SectionTitle icon="trending-up">Upgrade ideas</SectionTitle>
                {team.upgrade_ideas.map((p, i) => <PlayerRow expanded={expanded} onToggle={toggleRow} key={p.id} p={p} rank={i + 1} />)}
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

function Seg({ options, value, onPick }) {
  return (
    <View style={s.segRow}>
      {options.map(([k, label]) => (
        <Pressable key={k} onPress={() => onPick(k)} style={[s.segBtn, value === k && s.segBtnOn]}
          accessibilityRole="button" accessibilityState={{ selected: value === k }}>
          <Text style={[s.segTxt, value === k && { color: C.onAccent, fontWeight: "700" }]}>{label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function InfoRow({ label, value }) {
  return (
    <View style={s.infoRow}>
      <Text style={s.smallLabel}>{label}</Text>
      <Text style={[s.probLabel, TNUM, { textAlign: "right", flexShrink: 1 }]}>{value}</Text>
    </View>
  );
}

function SettingsScreen({ server, srv, oddsFormat, bankroll, fplId, saveSettings, resetSettings, meta, nbaMeta }) {
  const [showAbout, setShowAbout] = useState(false);
  const [fplDraft, setFplDraft] = useState(fplId);
  const [serverDraft, setServerDraft] = useState(server);
  const [bankrollDraft, setBankrollDraft] = useState(bankroll);
  const status = srv === "ok" ? "connected" : srv === "down" ? "not responding" : "connecting";

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
    <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled">
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
            placeholderTextColor={C.muted} accessibilityLabel="Bankroll" />
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
            placeholder="http://192.168.1.20:8710" placeholderTextColor={C.muted}
            accessibilityLabel="Server address" />
          <Pressable style={s.btn} onPress={() => saveSettings({ server: serverDraft.trim() })}>
            <Text style={s.btnTxt}>Save</Text>
          </Pressable>
        </View>
        <Text style={[s.axisNote, { marginTop: 8 }]}>current: {server}  ·  {status}</Text>
      </Card>

      <Card>
        <SectionTitle icon="database">Data &amp; model</SectionTitle>
        <InfoRow label="results through" value={meta ? meta.data_to : "–"} />
        <InfoRow label="matches in database" value={meta ? meta.matches.toLocaleString() : "–"} />
        <InfoRow label="model accuracy (latest test)"
          value={meta?.live_eval ? `${(meta.live_eval.model_accuracy * 100).toFixed(1)}%` : "–"} />
        <InfoRow label="NBA results through" value={nbaMeta?.data_to || "–"} />
        <InfoRow label="data refresh" value={meta?.refresh?.auto ? `automatic, ${meta.refresh.auto}` : "automatic"} />
      </Card>

      <Card>
        <SectionTitle icon="heart">Play it safe</SectionTitle>
        <Text style={s.dimTxt}>
          Only bet money you can afford to lose, and take breaks. If it stops being fun,
          call 1-800-GAMBLER. Free, confidential, always open. The About page has the full
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
            placeholderTextColor={C.muted} accessibilityLabel="Fantasy team ID" />
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
          <AboutModalBody onClose={() => setShowAbout(false)} meta={meta} nbaMeta={nbaMeta} />
        </Modal>
      </Card>

      <Pressable style={[s.detailsBtn, { alignSelf: "center", marginTop: 24 }]}
        onPress={() => {
          resetSettings();
          setServerDraft(DEFAULT_SERVER); setBankrollDraft(""); setFplDraft("");
        }}>
        <Feather name="rotate-ccw" size={14} color={C.dim} />
        <Text style={[s.detailsBtnTxt, { color: C.dim }]}>  Reset all settings</Text>
      </Pressable>
    </ScrollView>
    </KeyboardAvoidingView>
  );
}

function AboutModalBody({ onClose, meta, nbaMeta }) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.root, { paddingTop: Math.max(insets.top, 24) + 6, paddingBottom: insets.bottom }]}>
      <View style={s.rowBetween}>
        <Text style={[s.h2, { padding: 20, marginBottom: 0 }]}>About Plus100</Text>
        <Pressable onPress={onClose} style={{ padding: 20 }} accessibilityLabel="Close">
          <Feather name="x" size={22} color={C.chalk} />
        </Pressable>
      </View>
      <AboutScreen meta={meta} nbaMeta={nbaMeta} />
    </View>
  );
}

/* ================= about ================= */

function AboutSection({ title, children }) {
  return (
    <View>
      <Text style={s.h3}>{title}</Text>
      <Text style={s.aboutTxt}>{children}</Text>
    </View>
  );
}

function AboutScreen({ meta, nbaMeta }) {
  const live = meta?.live_eval;
  const nm = nbaMeta?.eval && nbaMeta?.fit ? nbaMeta : null;
  const fe = meta?.fantasy_eval;
  return (
    <ScrollView contentContainerStyle={s.scroll}>
      <Text style={s.h2}>What Plus100 is (and isn't)</Text>
      <Text style={s.aboutTxt}>
        Plus100 is a statistics tool. It estimates the probability of football and NBA outcomes from
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
      {nm && (
        <AboutSection title="Basketball (NBA)">
          Replayed on the most recent full season without peeking ({nm.eval.games} games), the model picked
          the winner {(nm.eval.accuracy * 100).toFixed(1)}% of the time and missed the spread by {nm.eval.spread_mae} points
          on average.{nm.fit.points_per_elo ? ` One point of margin per ${(1 / nm.fit.points_per_elo).toFixed(0)} rating points,` : ""}
          {" "}home court worth {nm.fit.home_court_pts}, margin spread {nm.fit.sd_margin}, total spread {nm.fit.sd_total}
          {nm.fit.seasons?.length ? `, fitted on ${nm.fit.seasons.length} seasons` : ""}.
          Results {nm.data_from} to {nm.data_to}, refreshed daily; injuries and rest priced from the live report.
        </AboutSection>
      )}
      <AboutSection title="Probabilities are not certainty">
        A 60% chance fails 4 times out of 10. Even the strongest "good bet" flagged here loses
        regularly. Edges only show up as profit across many bets. There is no 100% win rate,
        here or anywhere.
      </AboutSection>
      <AboutSection title="Measured, honest accuracy">
        {live
          ? `Accuracy is re-measured automatically at every data refresh. In the latest window the model called ${(live.model_accuracy * 100).toFixed(1)}% of results correctly. For scale: random guessing on three outcomes gets 33%, and the bookmakers themselves get ${(live.book_accuracy * 100).toFixed(1)}%. `
          : "Accuracy is re-measured automatically at every data refresh. "}
        Probabilities are calibrated: when the model says 40%, it happens about 40% of the time.
      </AboutSection>
      <AboutSection title="How good the fantasy projections are">
        {fe?.measured !== false && fe?.pairs
          ? `Measured on ${fe.pairs} real player-seasons, judged against what those players actually scored the FOLLOWING season. Ranking players by shot quality alone scores ${Number(fe.xg_only).toFixed(2)} out of 1; by their own scoring record alone ${Number(fe.record_only).toFixed(2)}; the blend of both that this app uses, ${Number(fe.blended).toFixed(2)}. `
          : fe?.note ? `${fe.note} ` : ""}
        Fantasy is more predictable than match results, because a player's own
        record carries over from season to season in a way that single match
        outcomes never do.
      </AboutSection>
      <AboutSection title="Where edges really come from">
        Against closing prices nothing out-predicts the market, including this model.
        We tested that properly rather than assuming it: on thousands of unseen
        matches, feeding our model's view into the closing price made predictions
        slightly worse, not better. Edges
        come from disagreements BETWEEN books, soft early prices, and boosts. That
        is what the Vs Market tab hunts.
      </AboutSection>
      <AboutSection title="No liability">
        You alone decide whether, where, and how much to bet. Plus100 and its author accept no
        responsibility for any losses. Nothing here is financial advice. Use only where legal
        and only at legal age.
      </AboutSection>
      <AboutSection title="Bet responsibly">
        Never stake money you cannot afford to lose. Suggested stakes are deliberately small fractions of a bankroll: treat them as caps, not targets.
        If gambling stops being fun, call 1-800-GAMBLER. Free, confidential, and open 24/7.
      </AboutSection>
    </ScrollView>
  );
}

/* ================= the math (details modal) ================= */

function Para({ children }) { return <Text style={s.aboutTxt}>{children}</Text>; }
function Key({ children }) { return <Text style={{ color: C.lime, fontWeight: "700" }}>{children}</Text>; }

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
  const recentSays = `${md.dc[0]} to ${md.dc[1]}`;
  const ratingsSay = `${md.elo[0]} to ${md.elo[1]}`;
  const agree = Math.abs(md.dc[0] - md.elo[0]) < 0.25 && Math.abs(md.dc[1] - md.elo[1]) < 0.25;
  const eloW = md.elo_weight;
  const top = p.markets.correct_scores[0];
  const p00 = p.score_matrix[0][0];
  const favSide = m.home >= m.away ? "home" : "away";
  const favName = favSide === "home" ? p.home.name : p.away.name;
  const favProb = Math.max(m.home, m.away);
  const favFair = m.fair_odds[favSide];
  const absent = [...(p.absences?.home || []).map((n) => `${n} (${p.home.name})`),
                  ...(p.absences?.away || []).map((n) => `${n} (${p.away.name})`)];

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
            {" "}{meta ? `${meta.matches.toLocaleString()}-match` : "full-history"} database, with recent
            games counting the most.
          </Para>
          <Para>
            Here the gap is <Key>{gap} points in favor of {stronger}</Key>. This gap
            is the single biggest input to the prediction.
          </Para>

          <SectionTitle accent icon="users">Step 2 · Who can actually play?</SectionTitle>
          <Para>
            Before any goals are estimated, we assemble each side's probable players from
            the official squad lists, then remove anyone the league's live availability
            flags or the day's team news say is out or doubtful. A missing player takes
            his usual share of his team's goals with him, and the pitch view shows who remains.
            {absent.length > 0 ? ` Priced out of this match: ${absent.join(", ")}.` : ""}
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
            {" "}We combine them{eloW != null ? `, giving the ratings view ${Math.round(eloW * 100)}% of the weight (a value fitted on past results, not guessed)` : ""},
            and land on <Key>{lH} goals for {p.home.name}</Key> and
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
            {" "}<Key>{fmtOdds(favFair)}</Key>. If a sportsbook pays more than that, the price is in your
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
const makeStyles = (C) => { const nba = C.sport === "basketball"; const rad = (n) => (nba ? Math.round(n * 0.62) : n); return StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingVertical: 14, gap: 12 },
  sportSw: { flexDirection: "row", backgroundColor: C.panel2, borderRadius: 11, padding: 3 },
  sportBtn: { paddingHorizontal: 11, paddingVertical: 6, borderRadius: 8, minHeight: 36, justifyContent: "center" },
  sportBtnOn: { backgroundColor: C.panel, shadowColor: "#000", shadowOpacity: 0.08, shadowRadius: 4, elevation: 1 },
  sportTxt: { fontSize: 12.5, fontWeight: "600", color: C.dim },
  sportTxtOn: { color: nba ? C.limeDeep : C.lime, fontWeight: "800" },
  logoMark: { width: 34, height: 34, borderRadius: 10, backgroundColor: C.lime,
    alignItems: "center", justifyContent: "center" },
  logoPlus: { color: C.onAccent, fontSize: 24, fontWeight: "800", marginTop: -2 },
  wordmark: { color: C.chalk, fontSize: 22, fontWeight: "800", letterSpacing: -0.4 },
  srvBanner: { flexDirection: "row", alignItems: "center", gap: 10, marginHorizontal: 20,
    marginBottom: 8, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 12,
    backgroundColor: C.panel, borderWidth: 1, borderColor: C.line },
  srvBannerTxt: { color: C.dim, fontSize: 13, lineHeight: 18, flex: 1 },
  nav: { flexDirection: "row", borderTopWidth: 1, borderTopColor: C.line,
    backgroundColor: nba ? C.panel : C.bg, paddingHorizontal: 8, paddingTop: 6 },
  navBtn: { flex: 1, alignItems: "center", paddingVertical: 9, gap: 3, minHeight: 52,
    borderRadius: 13, marginHorizontal: 2 },
  navBtnOn: { backgroundColor: C.limeDim },
  navTxt: { color: C.muted, fontSize: 11, fontWeight: "600", letterSpacing: 0.2 },
  scroll: { padding: 20, paddingBottom: 56 },

  h2: nba ? { ...NUM, color: C.chalk, fontSize: 30, letterSpacing: 0.4, textTransform: "uppercase", marginBottom: 10 }
    : { color: C.chalk, fontSize: 24, fontWeight: "800", letterSpacing: -0.5, marginBottom: 10 },
  h3: nba ? { color: C.dim, fontSize: 12, fontWeight: "700", letterSpacing: 1.6, textTransform: "uppercase" }
    : { color: C.chalk, fontSize: 15, fontWeight: "700", letterSpacing: 0.1 },
  secTitleRow: { flexDirection: "row", alignItems: "center", marginTop: 6, marginBottom: 13 },
  secNote: { color: C.muted, fontSize: 12, marginLeft: "auto" },
  dimTxt: { color: C.dim, fontSize: 14.5, lineHeight: 22 },
  smallLabel: { color: C.dim, fontSize: 13 },
  errTxt: { color: C.red, fontSize: 14, marginTop: 12, lineHeight: 21 },

  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: rad(18),
    padding: nba ? 16 : 20, marginTop: 16 },
  cardGood: { borderColor: C.pitch, backgroundColor: C.pitchDim },
  tileRow: { flexDirection: "row", gap: 10, marginTop: 18 },
  tile: { flex: 1, backgroundColor: C.panel2, borderRadius: rad(14), paddingVertical: 14,
    paddingHorizontal: 8, alignItems: "center" },
  tileVal: { color: C.chalk, fontSize: 19, fontWeight: "700" },
  tileLabel: { color: C.muted, fontSize: 11, marginTop: 4, textAlign: "center" },
  tileSub: { color: C.dim, fontSize: 12 },

  slotRow: { flexDirection: "row", alignItems: "stretch", gap: 10 },
  slotCard: { flex: 1, backgroundColor: C.panel, borderWidth: 1.5, borderColor: C.line,
    borderRadius: rad(20), paddingVertical: 20, paddingHorizontal: 10, alignItems: "center",
    justifyContent: "center", minHeight: 136 },
  slotCardOn: { borderColor: C.lime, backgroundColor: C.limeDim },
  slotBadge: { width: 54, height: 54, borderRadius: 27 },
  slotBadgePh: { width: 54, height: 54, borderRadius: 27, backgroundColor: C.panel2,
    alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: C.line },
  slotName: { color: C.chalk, fontSize: 15, fontWeight: "700", marginTop: 10,
    textAlign: "center" },
  slotSub: { color: C.muted, fontSize: 12, marginTop: 3 },
  vsWrap: { alignItems: "center", justifyContent: "center", width: 52 },
  vsTxt: nba ? { ...NUM, color: C.muted, fontSize: 17, letterSpacing: 1.5 }
    : { color: C.dim, fontSize: 17, fontWeight: "900", fontStyle: "italic", letterSpacing: 1 },
  cta: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 9,
    borderRadius: rad(16), minHeight: 54, marginTop: 14, overflow: nba ? "visible" : "hidden" },
  ctaTxt: { color: C.onAccent, fontSize: 16.5, fontWeight: "800", letterSpacing: 0.2 },

  heroCard: { borderColor: C.heroBorder, overflow: "hidden", backgroundColor: C.heroBg },
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
  bannerKicker: { color: C.bannerKicker, fontSize: 12, fontWeight: "800", letterSpacing: 2.2 },
  bannerTitle: { color: "#FFFFFF", fontSize: 20, fontWeight: "900", letterSpacing: -0.4,
    marginTop: 5, lineHeight: 25 },
  bannerSub: { color: C.bannerSub, fontSize: 12.5, marginTop: 7 },

  mqCard: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: rad(16),
    paddingHorizontal: 14, paddingVertical: 12, width: nba ? 196 : 168 },
  mqTag: { color: C.lime, fontSize: 12, fontWeight: "800", letterSpacing: 0.3 },
  mqNames: { color: C.dim, fontSize: 12, marginTop: 8 },


  dotWrap: { width: 44, height: 44, borderRadius: 22, borderWidth: 2,
    backgroundColor: "rgba(7,10,20,0.55)", alignItems: "center", justifyContent: "center",
    overflow: "hidden" },
  dotImg: { width: 40, height: 40, borderRadius: 20 },
  dotInitials: { color: C.chalk, fontSize: 14, fontWeight: "800" },
  dotName: { color: "#FFFFFF", fontSize: 9.5, fontWeight: "700", marginTop: 3,
    textShadowColor: "rgba(0,0,0,0.85)", textShadowRadius: 3, textShadowOffset: { width: 0, height: 1 } },
  dotBadge: { position: "absolute", top: -6, right: -14, minWidth: 30, height: 18,
    borderRadius: 9, paddingHorizontal: 5, alignItems: "center", justifyContent: "center",
    zIndex: 3, borderWidth: 1, borderColor: "rgba(255,255,255,0.7)" },
  dotBadgeTxt: { color: "#FFFFFF", fontSize: 12, fontWeight: "800" },
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
  benchName: { color: C.dim, fontSize: 12, marginTop: 2, maxWidth: 54 },
  fplFace: { width: 32, height: 32, borderRadius: 16 },
  input: { backgroundColor: C.panel2, borderRadius: rad(12), borderWidth: 1, borderColor: C.line,
    color: C.chalk, fontSize: 17, paddingHorizontal: 16, paddingVertical: 14, minHeight: 48 },
  dropdown: { backgroundColor: C.panel, borderRadius: rad(12), borderWidth: 1, borderColor: C.line, marginTop: 6 },
  opt: { paddingHorizontal: 16, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: C.line,
    minHeight: 48 },
  optName: { color: C.chalk, fontSize: 16, fontWeight: "600" },
  optSub: { color: C.muted, fontSize: 13, marginTop: 4 },

  row: { flexDirection: "row", alignItems: "center" },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  rowWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  btn: { backgroundColor: C.lime, borderRadius: rad(14), paddingHorizontal: 24, paddingVertical: 14,
    minHeight: 48, justifyContent: "center" },
  btnOff: { opacity: 0.4 },
  btnTxt: { color: C.onAccent, fontWeight: "700", letterSpacing: 0.2, fontSize: 15.5 },

  ctxNote: { flexDirection: "row", alignItems: "flex-start", marginTop: 10,
    backgroundColor: C.panel2, borderRadius: 12, padding: 12 },
  ctxNoteTxt: { color: C.dim, fontSize: 12.5, lineHeight: 18, flex: 1 },

  swapBtn: { alignItems: "center", justifyContent: "center", alignSelf: "center",
    marginTop: 8, width: 36, height: 36, borderWidth: 1, borderColor: C.line,
    borderRadius: 999, backgroundColor: C.panel },
  slotSideTag: { alignSelf: "center", backgroundColor: C.limeDim,
    borderWidth: 1, borderColor: C.lime, borderRadius: 999,
    paddingHorizontal: 10, paddingVertical: 2, marginBottom: 8 },
  slotSideTxt: { color: C.limeDeep, fontSize: 10, fontWeight: "800", letterSpacing: 1.6 },
  slotSideTagNba: { backgroundColor: "transparent", borderWidth: 0, paddingHorizontal: 0, marginBottom: 6 },
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

  barTrack: { backgroundColor: C.panel2, overflow: "hidden", flexGrow: 1, flexShrink: 1 },
  pairRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 7 },
  pairTag: { color: C.muted, fontSize: 11.5, width: 64, letterSpacing: 0.2 },
  pairVal: { color: C.chalk, fontSize: 13, width: 40, textAlign: "right" },

  heatRow: { flexDirection: "row", gap: 4, marginBottom: 4 },
  heatCell: { flex: 1, aspectRatio: 1.3, borderRadius: 6, alignItems: "center", justifyContent: "center" },
  heatHdr: { flex: 1, aspectRatio: 1.3, alignItems: "center", justifyContent: "center" },
  heatHdrTxt: { color: C.muted, fontSize: 12 },
  heatTxt: { fontSize: 12, fontWeight: "700" },
  axisNote: { color: C.muted, fontSize: 12, marginTop: 10, lineHeight: 17 },
  legendTxt: { color: C.dim, fontSize: 12 },

  /* market rows: the label takes what is left and wraps; the bar keeps a fixed basis */
  mktRow: { flexDirection: "row", alignItems: "center", marginBottom: 12 },
  mktLabel: { color: C.chalk, fontSize: 13.5, lineHeight: 18, flex: 1 },
  mktBar: { flexBasis: 90, flexGrow: 0, flexShrink: 0, marginHorizontal: 10 },
  mktVal: nba ? { ...NUM, color: C.chalk, fontSize: 15, width: 44, textAlign: "right" }
    : { color: C.dim, fontSize: 13, width: 38, textAlign: "right" },
  mktFair: { color: C.sky, fontSize: 13, width: 46, textAlign: "right", fontWeight: "600" },

  /* player props: hairline rows, name column plus four fixed cells */
  propRow: { flexDirection: "row", alignItems: "center", paddingVertical: 8, gap: 4,
    borderTopWidth: 1, borderTopColor: C.line },
  propCell: { width: 44, alignItems: "center" },
  propName: { color: C.chalk, fontSize: 13.5, fontWeight: "600" },
  propLine: { color: C.chalk, fontSize: 15, lineHeight: 17 },
  propOver: { color: C.muted, fontSize: 13, lineHeight: 15, marginTop: 1 },

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
  emScale: { color: C.muted, fontSize: 12 },

  segRow: { flexDirection: "row", gap: 8, marginTop: 12 },
  segBtn: { flex: 1, paddingVertical: 12, borderRadius: rad(12), borderWidth: 1, borderColor: C.line,
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

  /* ---- basketball scoreboard ---- */
  nbBanner: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.line, borderRadius: 12,
    padding: 18, marginBottom: 16, flexDirection: "row", alignItems: "center" },
  nbKicker: { color: C.lime, fontSize: 12, fontWeight: "700", letterSpacing: 2 },
  nbTitle: { color: C.chalk, fontSize: 30, letterSpacing: 0.3, marginTop: 6, lineHeight: 32 },
  nbSub: { color: C.dim, fontSize: 13, lineHeight: 19, marginTop: 8 },
  sbTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 10,
    paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: C.line },
  sbTopTxt: { color: C.muted, fontSize: 11.5, fontWeight: "700", letterSpacing: 1.1 },
  sbRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 13,
    borderBottomWidth: 1, borderBottomColor: C.line },
  sbName: { color: C.chalk, fontSize: 16.5, fontWeight: "700", letterSpacing: -0.2 },
  sbSide: { color: C.muted, fontSize: 12, fontWeight: "700", letterSpacing: 1.8, marginTop: 3 },
  sbPct: { fontSize: 19, lineHeight: 22, letterSpacing: -0.2 },
  sbFair: { color: C.muted, fontSize: 12 },
  sbPts: { color: C.chalk, fontSize: 40, lineHeight: 42, letterSpacing: -1, textAlign: "right" },
  sbCap: { flexDirection: "row", justifyContent: "space-between", marginTop: 8 },
  sbCapTxt: { color: C.muted, fontSize: 12, fontWeight: "700", letterSpacing: 1.4 },
  sbCells: { flexDirection: "row", borderTopWidth: 1, borderTopColor: C.line, marginTop: 14 },
  sbCell: { flex: 1, paddingVertical: 12, paddingHorizontal: 6, borderRightWidth: 1, borderRightColor: C.line, alignItems: "center" },
  sbCellVal: { color: C.chalk, fontSize: 24, lineHeight: 26 },
  sbCellLbl: { color: C.muted, fontSize: 10.5, marginTop: 4, textAlign: "center" },
  sbNote: { color: C.dim, fontSize: 13, lineHeight: 19, marginTop: 12 },
  sbTeam: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: 10, marginTop: 12, marginBottom: 4 },
  sbTeamNote: { color: C.muted, fontSize: 12, flex: 1, textAlign: "right", lineHeight: 16 },
  lineRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 7, borderBottomWidth: 1, borderBottomColor: C.line },
  lineNum: { color: C.chalk, fontSize: 17, width: 54 },
  linePct: { color: C.dim, fontSize: 15, width: 44, textAlign: "right" },
  lineHd: { color: C.muted, fontSize: 10.5, fontWeight: "700", letterSpacing: 1.4 },
  ctaNba: { backgroundColor: C.lime, shadowColor: C.lime, shadowOpacity: 0.35, shadowRadius: 14,
    shadowOffset: { width: 0, height: 0 }, elevation: 6 },
}); };
const s0 = makeStyles(C_FOOTBALL);
const sb = makeStyles(CB);   // basketball screens
let s = s0;
