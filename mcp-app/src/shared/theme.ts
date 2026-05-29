// Elastic brand design tokens for cluster triage views

export const theme = {
  bg: "#07080F",
  bgSecondary: "#0D1117",
  bgTertiary: "#161B22",
  border: "#21262D",
  borderStrong: "#30363D",
  text: "#E6EDF3",
  textMuted: "#7D8590",
  textFaint: "#484F58",
  teal: "#00BFB3",
  blue: "#0077CC",
  blueLight: "#58A6FF",
  yellow: "#FEC514",
  green: "#54B399",
  greenSoft: "#56D364",
  red: "#F85149",
  orange: "#F0883E",
  purple: "#BC8CFF",
  // Status colors
  statusGreen: "#2EA043",
  statusYellow: "#D29922",
  statusRed: "#DA3633",
};

export const baseStyles = `
  *, *::before, *::after { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 0;
    background: ${theme.bg};
    color: ${theme.text};
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  .mono {
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  }
  .ds-view {
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
`;

export function applyTheme() {
  const el = document.documentElement;
  el.style.setProperty("--bg", theme.bg);
  el.style.setProperty("--text", theme.text);
}
