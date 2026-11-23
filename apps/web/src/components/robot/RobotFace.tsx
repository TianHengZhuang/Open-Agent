'use client';

import * as React from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';

/**
 * Shared numeric face state. The body animation writes it every frame; the
 * canvas texture reads it. Keeping it mutable avoids React renders at 60 fps.
 */
export interface FaceFrame {
  eyeOpen: number;
  eyeCurve: number;
  mouthOpen: number;
  mouthCurve: number;
  gazeX: number;
  gazeY: number;
  color: THREE.Color;
}

export function createFaceFrame(): FaceFrame {
  return {
    eyeOpen: 1,
    eyeCurve: 0,
    mouthOpen: 0,
    mouthCurve: 0.3,
    gazeX: 0,
    gazeY: 0,
    color: new THREE.Color('#ffb45c'),
  };
}

const WIDTH = 640;
const HEIGHT = 360;

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

/**
 * OLED face drawn into a CanvasTexture. Eyes and mouth are vector shapes, so
 * blinking, gaze, smiling and lip-sync all interpolate without morph targets.
 */
export function RobotFace({ frame }: { frame: FaceFrame }) {
  const canvas = React.useMemo(() => {
    const element = document.createElement('canvas');
    element.width = WIDTH;
    element.height = HEIGHT;
    return element;
  }, []);

  const texture = React.useMemo(() => {
    const value = new THREE.CanvasTexture(canvas);
    value.colorSpace = THREE.SRGBColorSpace;
    value.minFilter = THREE.LinearFilter;
    value.magFilter = THREE.LinearFilter;
    value.generateMipmaps = false;
    return value;
  }, [canvas]);

  React.useEffect(() => () => texture.dispose(), [texture]);

  const lastDraw = React.useRef(-1);

  useFrame(({ clock }) => {
    const now = clock.elapsedTime;
    // 30 fps is indistinguishable on a glowing screen and leaves frame time
    // for the robot itself.
    if (now - lastDraw.current < 1 / 30) return;
    lastDraw.current = now;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    const color = `#${frame.color.getHexString()}`;
    const glow = Math.max(0.35, Math.min(1.15, frame.eyeOpen));
    ctx.shadowColor = color;
    ctx.shadowBlur = 24 * glow;
    ctx.fillStyle = color;
    ctx.strokeStyle = color;

    const leftX = 214;
    const rightX = 426;
    const eyeY = 146;
    const radius = 58;
    const open = Math.max(0, Math.min(1.2, frame.eyeOpen));
    const curve = Math.max(-1, Math.min(1, frame.eyeCurve));
    const gazeX = Math.max(-14, Math.min(14, frame.gazeX));
    const gazeY = Math.max(-12, Math.min(12, frame.gazeY));

    const eye = (cx: number) => {
      if (open < 0.1) {
        ctx.lineWidth = 13;
        ctx.beginPath();
        ctx.moveTo(cx - radius * 0.55, eyeY);
        ctx.lineTo(cx + radius * 0.55, eyeY);
        ctx.stroke();
        return;
      }

      if (curve > 0.38) {
        // Happy/success: upward arcs (∩ ∩).
        ctx.lineWidth = 15;
        ctx.beginPath();
        ctx.moveTo(cx - radius * 0.8, eyeY + 14);
        ctx.quadraticCurveTo(cx, eyeY - radius * 0.72, cx + radius * 0.8, eyeY + 14);
        ctx.stroke();
        return;
      }

      if (curve < -0.38) {
        // Sad/error: drooping lids.
        ctx.lineWidth = 14;
        ctx.beginPath();
        ctx.moveTo(cx - radius * 0.72, eyeY + 8);
        ctx.quadraticCurveTo(cx, eyeY + radius * 0.62, cx + radius * 0.72, eyeY + 8);
        ctx.stroke();
        return;
      }

      // Open OLED eye with a soft inner core and a specular dot.
      const height = radius * 0.9 * open;
      ctx.beginPath();
      ctx.ellipse(cx + gazeX, eyeY + gazeY, radius * 0.62, height, 0, 0, Math.PI * 2);
      ctx.fill();

      ctx.shadowBlur = 0;
      ctx.fillStyle = 'rgba(255, 249, 228, 0.92)';
      ctx.beginPath();
      ctx.ellipse(
        cx + gazeX - radius * 0.17,
        eyeY + gazeY - height * 0.3,
        radius * 0.14,
        radius * 0.18 * Math.min(1, open),
        0,
        0,
        Math.PI * 2,
      );
      ctx.fill();
      ctx.fillStyle = color;
      ctx.shadowBlur = 24 * glow;
    };

    eye(leftX);
    eye(rightX);

    const mouthY = 264;
    const smile = Math.max(-1, Math.min(1, frame.mouthCurve));
    const mouthOpen = Math.max(0, Math.min(1, frame.mouthOpen));

    if (mouthOpen > 0.07) {
      const width = 50 + smile * 12;
      const height = 12 + mouthOpen * 52;
      ctx.beginPath();
      ctx.ellipse(WIDTH / 2, mouthY + 4, width, height, 0, 0, Math.PI * 2);
      ctx.fill();
      // A darker inner mouth gives the glow some depth while speaking.
      ctx.shadowBlur = 0;
      ctx.fillStyle = 'rgba(46, 18, 2, 0.55)';
      ctx.beginPath();
      ctx.ellipse(WIDTH / 2, mouthY + 7, width * 0.58, Math.max(2, height * 0.48), 0, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.lineWidth = 13;
      ctx.beginPath();
      if (smile > 0.25) {
        ctx.moveTo(WIDTH / 2 - 48, mouthY + 2);
        ctx.quadraticCurveTo(WIDTH / 2, mouthY + 34 * smile, WIDTH / 2 + 48, mouthY + 2);
      } else if (smile < -0.25) {
        ctx.moveTo(WIDTH / 2 - 46, mouthY + 14);
        ctx.quadraticCurveTo(WIDTH / 2, mouthY - 26 * Math.abs(smile), WIDTH / 2 + 46, mouthY + 14);
      } else {
        ctx.moveTo(WIDTH / 2 - 38, mouthY + 6);
        ctx.quadraticCurveTo(WIDTH / 2, mouthY + 12, WIDTH / 2 + 38, mouthY + 6);
      }
      ctx.stroke();
    }

    // Very subtle scanlines keep the display from looking like flat paint.
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 0.045;
    ctx.fillStyle = '#000000';
    for (let y = 0; y < HEIGHT; y += 4) {
      ctx.fillRect(0, y, WIDTH, 1);
    }
    ctx.globalAlpha = 1;

    texture.needsUpdate = true;
  });

  return (
    <mesh position={[0, 0, 0.021]}>
      <planeGeometry args={[2.08, 1.17]} />
      <meshBasicMaterial
        map={texture}
        transparent
        toneMapped={false}
        depthWrite={false}
        opacity={0.98}
      />
    </mesh>
  );
}
