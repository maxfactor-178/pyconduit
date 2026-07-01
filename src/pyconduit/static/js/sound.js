// Notification sound via the Web Audio API — no asset files needed.
// A short two-tone blip; respects the user's sound setting (checked by caller).
window.Sound = (function () {
  let ctx = null;

  function ensureCtx() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) ctx = new AC();
    }
    return ctx;
  }

  function blip() {
    const c = ensureCtx();
    if (!c) return;
    if (c.state === "suspended") c.resume();
    const now = c.currentTime;
    [660, 880].forEach((freq, i) => {
      const osc = c.createOscillator();
      const gain = c.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const t = now + i * 0.09;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.12, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.08);
      osc.connect(gain).connect(c.destination);
      osc.start(t);
      osc.stop(t + 0.09);
    });
  }

  return { blip };
})();
