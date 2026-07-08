// Notification sounds via the Web Audio API — no asset files (CSP-friendly).
// The caller checks the user's sound setting before playing.
window.Sound = (function () {
  let ctx = null;

  function ensureCtx() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) ctx = new AC();
    }
    return ctx;
  }

  // Play a short sequence of notes: [{freq, gain}], each `step` seconds apart.
  function tones(notes, step) {
    const c = ensureCtx();
    if (!c) return;
    if (c.state === "suspended") c.resume();
    const now = c.currentTime;
    notes.forEach((n, i) => {
      const osc = c.createOscillator();
      const gain = c.createGain();
      osc.type = "sine";
      osc.frequency.value = n.freq;
      const t = now + i * step;
      const peak = n.gain || 0.12;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(peak, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + step - 0.01);
      osc.connect(gain).connect(c.destination);
      osc.start(t);
      osc.stop(t + step);
    });
  }

  // Generic message: soft two-tone blip.
  function blip() {
    tones([{ freq: 660 }, { freq: 880 }], 0.09);
  }

  // Being @-mentioned: brighter, three-note ascending chime so it stands out.
  function mention() {
    tones([{ freq: 587, gain: 0.14 }, { freq: 784, gain: 0.14 }, { freq: 1047, gain: 0.16 }], 0.11);
  }

  return { blip, mention };
})();
