// Moth Phone Page Controller
// Self-contained implementation complying with HANDOFF.md §4 & §5

(function() {
  let pin = localStorage.getItem('moth_pin') || '';
  let currentState = null;
  let currentWalkIndex = 0;
  let isWalking = false;
  let nextIsDetour = false;
  let selectedDot = null;
  let selectedSquare = null;

  // Sensor state
  let motionActive = false;
  let stepCount = 0;
  let isMoving = 0;
  let currentHeading = null;
  let lastAccelMag = 0;
  let accelSpread = [];
  let wakeLock = null;

  // DOM Elements
  const pinOverlay = document.getElementById('pinOverlay');
  const pinInput = document.getElementById('pinInput');
  const pinSubmitBtn = document.getElementById('pinSubmitBtn');
  const pinError = document.getElementById('pinError');
  const pinBadge = document.getElementById('pinBadge');
  const pinDisplay = document.getElementById('pinDisplay');
  const sensorDot = document.getElementById('sensorDot');
  const motionStatus = document.getElementById('motionStatus');
  const stepCountEl = document.getElementById('stepCount');
  const headingDisplay = document.getElementById('headingDisplay');
  const enableMotionBtn = document.getElementById('enableMotionBtn');
  const noticeBox = document.getElementById('noticeBox');
  const phoneMapSvg = document.getElementById('phoneMapSvg');
  const modeTitle = document.getElementById('modeTitle');
  const walkBadge = document.getElementById('walkBadge');
  const instructionText = document.getElementById('instructionText');
  const nextDotBtn = document.getElementById('nextDotBtn');
  const skipDotBtn = document.getElementById('skipDotBtn');
  const saveWalkBtn = document.getElementById('saveWalkBtn');
  const cancelWalkBtn = document.getElementById('cancelWalkBtn');
  const checkSpotBtn = document.getElementById('checkSpotBtn');
  const recordSpotBtn = document.getElementById('recordSpotBtn');
  const orientationSelect = document.getElementById('orientationSelect');
  const emptyRoomBtn = document.getElementById('emptyRoomBtn');
  const clearCalibBtn = document.getElementById('clearCalibBtn');

  // Notice Toast helper
  function showNotice(msg, duration = 4000) {
    if (!noticeBox) return;
    noticeBox.textContent = msg;
    noticeBox.classList.remove('hidden');
    setTimeout(() => {
      noticeBox.classList.add('hidden');
    }, duration);
  }

  // PIN Management
  function updatePinDisplay() {
    if (pinDisplay) pinDisplay.textContent = pin ? '••••' : 'Set PIN';
  }

  function requirePin(errorMsg = '') {
    if (pinOverlay) {
      pinOverlay.classList.remove('hidden');
      if (pinInput) {
        pinInput.value = '';
        pinInput.focus();
      }
      if (pinError) pinError.textContent = errorMsg;
    }
  }

  pinBadge?.addEventListener('click', () => requirePin());

  pinSubmitBtn?.addEventListener('click', () => {
    const val = pinInput.value.trim();
    if (val.length === 4) {
      pin = val;
      localStorage.setItem('moth_pin', pin);
      pinOverlay.classList.add('hidden');
      updatePinDisplay();
      fetchState();
    } else {
      if (pinError) pinError.textContent = 'Enter a valid 4-digit PIN';
    }
  });

  if (!pin) {
    requirePin();
  } else {
    updatePinDisplay();
  }

  // 10x per second PING loop (HANDOFF.md §4: traffic makes phone trackable)
  setInterval(() => {
    fetch('/ping').catch(() => {});
  }, 100);

  // Poll /api/state?pin= every 500ms
  async function fetchState() {
    if (!pin) return;
    try {
      const res = await fetch(`/api/state?pin=${encodeURIComponent(pin)}`);
      if (res.status === 403) {
        // Wrong PIN -> stop and ask again
        requirePin('Incorrect PIN. Please re-enter.');
        return;
      }
      if (!res.ok) return;
      const data = await res.json();
      currentState = data;
      renderPhoneMap(data);
      updateStatusDisplay(data);
    } catch (e) {}
  }
  setInterval(fetchState, 500);
  fetchState();

  // Screen WakeLock
  async function requestWakeLock() {
    if ('wakeLock' in navigator) {
      try {
        wakeLock = await navigator.wakeLock.request('screen');
      } catch (err) {}
    }
  }

  // Enable Motion & Sensors
  enableMotionBtn?.addEventListener('click', async () => {
    requestWakeLock();
    // iOS permission gate
    if (typeof DeviceMotionEvent !== 'undefined' && typeof (DeviceMotionEvent as any).requestPermission === 'function') {
      try {
        const resp = await (DeviceMotionEvent as any).requestPermission();
        if (resp !== 'granted') {
          showNotice('Motion permission denied');
          return;
        }
      } catch (e) {}
    }

    if (typeof DeviceOrientationEvent !== 'undefined' && typeof (DeviceOrientationEvent as any).requestPermission === 'function') {
      try {
        await (DeviceOrientationEvent as any).requestPermission();
      } catch (e) {}
    }

    // Motion listener
    window.addEventListener('devicemotion', (e) => {
      const acc = e.accelerationIncludingGravity || e.acceleration;
      if (!acc || acc.x === null) return;
      const mag = Math.hypot(acc.x || 0, acc.y || 0, acc.z || 0);

      accelSpread.push(mag);
      if (accelSpread.length > 20) accelSpread.shift();

      // Moving vs still: spread of acceleration over ~1s
      const minA = Math.min(...accelSpread);
      const maxA = Math.max(...accelSpread);
      isMoving = (maxA - minA > 2.2) ? 1 : 0;

      // Step detection: acceleration peaks
      if (mag > 12.0 && lastAccelMag <= 12.0) {
        stepCount++;
        if (stepCountEl) stepCountEl.textContent = stepCount.toString();
      }
      lastAccelMag = mag;
    });

    // Compass listener
    window.addEventListener('deviceorientation', (e: any) => {
      if (typeof e.webkitCompassHeading !== 'undefined') {
        currentHeading = Math.round(e.webkitCompassHeading);
      } else if (e.alpha !== null) {
        currentHeading = Math.round((360 - e.alpha) % 360);
      }
      if (headingDisplay && currentHeading !== null) {
        headingDisplay.textContent = `${currentHeading}°`;
      }
    });

    motionActive = true;
    if (sensorDot) {
      sensorDot.classList.remove('sensor-inactive');
      sensorDot.classList.add('sensor-active');
    }
    if (motionStatus) motionStatus.textContent = 'Motion Active';
    enableMotionBtn.classList.add('hidden');
    showNotice('Sensors enabled. Transmitting motion data.');
  });

  // Transmit motion every 250ms (HANDOFF.md §4)
  setInterval(() => {
    if (!pin || !motionActive) return;
    const body = {
      pin,
      mv: isMoving,
      st: stepCount,
      hd: currentHeading,
      ac: lastAccelMag
    };
    fetch('/api/motion', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).catch(() => {});
  }, 250);

  // Status text display
  function updateStatusDisplay(state) {
    if (!state) return;
    if (walkBadge) walkBadge.textContent = `Walks: ${state.walks || 0}`;

    if (!state.me) {
      instructionText.textContent = "Not connected · Join Wi-Fi: DEMO-DONT JOIN";
      return;
    }

    if (!state.route || state.route.length === 0) {
      instructionText.textContent = "Room not set up · Configure in Dashboard";
      return;
    }

    if (state.capture) {
      const cap = state.capture;
      instructionText.textContent = `${cap.kind.toUpperCase()}: ${cap.phase} (${Math.round(cap.duration - cap.elapsed)}s remaining)`;
    }
  }

  // Map & SVG Render
  function renderPhoneMap(state) {
    if (!phoneMapSvg || !state || !state.polygon || state.polygon.length === 0) return;

    const poly = state.polygon;
    const xs = poly.map(p => p[0]);
    const ys = poly.map(p => p[1]);
    const minX = Math.min(...xs, 0);
    const minY = Math.min(...ys, 0);
    const maxX = Math.max(...xs, 7.5);
    const maxY = Math.max(...ys, 5.0);
    const margin = 0.5;

    phoneMapSvg.setAttribute('viewBox', `${minX - margin} ${minY - margin} ${(maxX - minX) + margin * 2} ${(maxY - minY) + margin * 2}`);

    let svgHtml = '';

    // Room Polygon
    svgHtml += `<polygon points="${poly.map(p => `${p[0]},${p[1]}`).join(' ')}" fill="#12301D" stroke="rgba(255,255,255,0.4)" stroke-width="0.06"/>`;

    // Objects
    if (state.objects) {
      for (const obj of state.objects) {
        svgHtml += `<polygon points="${obj.footprint.map(p => `${p[0]},${p[1]}`).join(' ')}" fill="rgba(46,111,64,0.5)" stroke="rgba(255,255,255,0.2)" stroke-width="0.02"/>`;
      }
    }

    // Receivers
    if (state.boards) {
      for (const b of state.boards) {
        svgHtml += `<circle cx="${b.x}" cy="${b.y}" r="0.16" fill="#8FD3A5"/>`;
        svgHtml += `<text x="${b.x}" y="${b.y + 0.32}" font-size="0.2" fill="#FFFFFF" text-anchor="middle" font-family="monospace">${b.label}</text>`;
      }
    }

    // Accuracy Check Squares
    if (state.checks) {
      state.checks.forEach((chk, idx) => {
        const size = 0.35;
        svgHtml += `<rect data-chk="${idx}" x="${chk[0] - size/2}" y="${chk[1] - size/2}" width="${size}" height="${size}" fill="rgba(147, 197, 253, 0.25)" stroke="#93C5FD" stroke-width="0.03" rx="0.05" class="chk-square" style="cursor:pointer;"/>`;
      });
    }

    // Calibration Route Dots & Connecting Lines
    if (state.route && state.route.length > 0) {
      // Connect dots
      for (let i = 0; i < state.route.length - 1; i++) {
        const p1 = state.route[i];
        const p2 = state.route[i + 1];
        const isDetour = p2[2] === 1;
        svgHtml += `<line x1="${p1[0]}" y1="${p1[1]}" x2="${p2[0]}" y2="${p2[1]}" stroke="${isDetour ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.6)'}" stroke-width="0.03" stroke-dasharray="${isDetour ? '0.06 0.06' : 'none'}"/>`;
      }

      // Draw Route Dots
      state.route.forEach((dot, idx) => {
        const isCurrent = isWalking && idx === currentWalkIndex;
        const radius = isCurrent ? 0.22 : 0.16;
        svgHtml += `<circle data-dot="${idx}" cx="${dot[0]}" cy="${dot[1]}" r="${radius}" fill="${isCurrent ? '#8FD3A5' : 'rgba(255,255,255,0.3)'}" stroke="#FFFFFF" stroke-width="0.03" class="route-dot" style="cursor:pointer;"/>`;
        svgHtml += `<text x="${dot[0]}" y="${dot[1] + 0.06}" font-size="0.16" font-weight="bold" fill="${isCurrent ? '#12301D' : '#FFFFFF'}" text-anchor="middle" font-family="monospace">${idx + 1}</text>`;
      });
    }

    // My current estimated position (hollow circle when not walking, per HANDOFF.md §5)
    if (state.me && state.me.est && !isWalking) {
      const est = state.me.est;
      svgHtml += `<circle cx="${est.x}" cy="${est.y}" r="${est.unc || 0.4}" fill="none" stroke="#8FD3A5" stroke-width="0.04" stroke-dasharray="0.06 0.06"/>`;
      svgHtml += `<circle cx="${est.x}" cy="${est.y}" r="0.1" fill="none" stroke="#FFFFFF" stroke-width="0.04"/>`;
    }

    phoneMapSvg.innerHTML = svgHtml;

    // Attach click listeners to dots and check squares
    phoneMapSvg.querySelectorAll('.route-dot').forEach(el => {
      el.addEventListener('click', (e) => {
        const dotIdx = parseInt((e.currentTarget as HTMLElement).getAttribute('data-dot') || '0', 10);
        selectedDot = dotIdx;
        if (!isWalking) {
          startWalk(dotIdx);
        }
      });
    });

    phoneMapSvg.querySelectorAll('.chk-square').forEach(el => {
      el.addEventListener('click', (e) => {
        const chkIdx = parseInt((e.currentTarget as HTMLElement).getAttribute('data-chk') || '0', 10);
        selectedSquare = chkIdx;
        showNotice(`Selected check square #${chkIdx + 1}. Tap 'Check here' below.`);
      });
    });
  }

  // Walk Control Actions
  async function startWalk(startIndex = 0) {
    if (!pin) return requirePin();
    currentWalkIndex = startIndex;
    isWalking = true;
    nextIsDetour = false;

    const orientation = (orientationSelect as HTMLSelectElement)?.value || 'hand';

    await fetch('/api/walk/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin, orientation })
    });

    // Mark the first dot
    await markDot(startIndex, false);

    // Update UI controls
    nextDotBtn?.classList.remove('hidden');
    skipDotBtn?.classList.remove('hidden');
    saveWalkBtn?.classList.remove('hidden');
    cancelWalkBtn?.classList.remove('hidden');

    updateWalkControls();
  }

  function updateWalkControls() {
    if (!currentState?.route) return;
    const nextIdx = currentWalkIndex + 1;
    if (nextIdx < currentState.route.length) {
      nextDotBtn.textContent = `I'm on dot ${nextIdx + 1}`;
      instructionText.textContent = `Walk to dot #${nextIdx + 1} and tap below`;
    } else {
      nextDotBtn.textContent = `Finish walk & Save`;
      instructionText.textContent = `Last dot reached! Tap to finish.`;
    }
  }

  async function markDot(idx, isDetour = false) {
    if (!currentState?.route?.[idx]) return;
    const dot = currentState.route[idx];

    // Vibrate phone for tactile feedback (HANDOFF.md §5)
    if ('vibrate' in navigator) navigator.vibrate(60);

    await fetch('/api/walk/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pin,
        x: dot[0],
        y: dot[1],
        detour: isDetour
      })
    });

    currentWalkIndex = idx;
    updateWalkControls();
  }

  nextDotBtn?.addEventListener('click', async () => {
    if (!currentState?.route) return;
    const nextIdx = currentWalkIndex + 1;
    if (nextIdx < currentState.route.length) {
      await markDot(nextIdx, nextIsDetour);
      nextIsDetour = false;
    } else {
      // Completed all dots
      stopWalk();
    }
  });

  skipDotBtn?.addEventListener('click', () => {
    currentWalkIndex++;
    nextIsDetour = true; // detour flag set for next marked dot
    showNotice(`Skipped dot. Next dot will be marked as detour.`);
    updateWalkControls();
  });

  async function stopWalk() {
    await fetch('/api/walk/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin })
    });
    isWalking = false;
    nextDotBtn?.classList.add('hidden');
    skipDotBtn?.classList.add('hidden');
    saveWalkBtn?.classList.add('hidden');
    cancelWalkBtn?.classList.add('hidden');
    instructionText.textContent = "Walk saved! Tap a dot to begin another walk.";
    showNotice("Walk saved successfully!");
    fetchState();
  }

  saveWalkBtn?.addEventListener('click', stopWalk);

  cancelWalkBtn?.addEventListener('click', async () => {
    await fetch('/api/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin })
    });
    isWalking = false;
    nextDotBtn?.classList.add('hidden');
    skipDotBtn?.classList.add('hidden');
    saveWalkBtn?.classList.add('hidden');
    cancelWalkBtn?.classList.add('hidden');
    instructionText.textContent = "Walk cancelled.";
    showNotice("Walk cancelled.");
    fetchState();
  });

  // Check spot (8s capture)
  checkSpotBtn?.addEventListener('click', async () => {
    if (!pin) return requirePin();
    let x = 3.5, y = 2.5;
    if (selectedSquare !== null && currentState?.checks?.[selectedSquare]) {
      x = currentState.checks[selectedSquare][0];
      y = currentState.checks[selectedSquare][1];
    }
    const res = await fetch('/api/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin, kind: 'test', x, y, duration: 8 })
    });
    if (res.ok) {
      showNotice("Accuracy check started (8s). Hold phone still.");
    }
  });

  // Record spot (12s capture)
  recordSpotBtn?.addEventListener('click', async () => {
    if (!pin) return requirePin();
    let x = 3.5, y = 2.5;
    if (selectedDot !== null && currentState?.route?.[selectedDot]) {
      x = currentState.route[selectedDot][0];
      y = currentState.route[selectedDot][1];
    }
    const res = await fetch('/api/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin, kind: 'calibrate', x, y, duration: 12 })
    });
    if (res.ok) {
      showNotice("Standing spot capture started (12s countdown).");
    }
  });

  // Record empty room
  emptyRoomBtn?.addEventListener('click', async () => {
    if (!pin) return requirePin();
    const res = await fetch('/api/empty/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin })
    });
    if (res.ok) {
      showNotice("Please leave the room now! Empty room recording active.");
    }
  });

  // Clear calibration
  clearCalibBtn?.addEventListener('click', async () => {
    if (!pin) return requirePin();
    if (confirm("Reset and clear all calibration spots for this room?")) {
      await fetch('/api/calib/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin })
      });
      showNotice("All calibration data cleared.");
      fetchState();
    }
  });

})();

