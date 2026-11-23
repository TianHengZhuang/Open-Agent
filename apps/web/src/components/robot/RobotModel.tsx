'use client';

import * as React from 'react';
import { RoundedBox } from '@react-three/drei';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';

import type { RobotState } from '@/features/robot/robot-machine';
import { RobotFace, createFaceFrame } from './RobotFace';

interface Mood {
  eyeOpen: number;
  eyeCurve: number;
  mouthCurve: number;
  headTiltX: number;
  headTiltZ: number;
  armLZ: number;
  armRZ: number;
  armX: number;
  float: number;
  bounce: number;
  lean: number;
  chest: number;
  ear: number;
  color: string;
  wave: boolean;
}

const MOODS: Record<RobotState, Mood> = {
  idle: {
    eyeOpen: 1,
    eyeCurve: 0,
    mouthCurve: 0.35,
    headTiltX: 0,
    headTiltZ: 0,
    armLZ: -0.12,
    armRZ: 0.12,
    armX: 0.04,
    float: 1,
    bounce: 0,
    lean: 0,
    chest: 1.1,
    ear: 0.8,
    color: '#ffb45c',
    wave: false,
  },
  listening: {
    eyeOpen: 1.12,
    eyeCurve: 0,
    mouthCurve: 0.18,
    headTiltX: 0.035,
    headTiltZ: -0.075,
    armLZ: -0.2,
    armRZ: 0.16,
    armX: 0.06,
    float: 0.7,
    bounce: 0,
    lean: -0.015,
    chest: 1.9,
    ear: 2.6,
    color: '#ffd27a',
    wave: false,
  },
  thinking: {
    eyeOpen: 0.62,
    eyeCurve: 0,
    mouthCurve: 0.02,
    headTiltX: -0.03,
    headTiltZ: -0.14,
    armLZ: -0.08,
    armRZ: 0.08,
    armX: 0.1,
    float: 0.45,
    bounce: 0,
    lean: 0.01,
    chest: 1.5,
    ear: 1.15,
    color: '#ffc25e',
    wave: false,
  },
  speaking: {
    eyeOpen: 0.94,
    eyeCurve: 0.08,
    mouthCurve: 0.22,
    headTiltX: 0.01,
    headTiltZ: -0.025,
    armLZ: -0.16,
    armRZ: 0.32,
    armX: 0.12,
    float: 0.5,
    bounce: 0,
    lean: 0,
    chest: 1.45,
    ear: 1.35,
    color: '#ffc56a',
    wave: false,
  },
  happy: {
    eyeOpen: 0.92,
    eyeCurve: 1,
    mouthCurve: 0.9,
    headTiltX: -0.02,
    headTiltZ: 0.06,
    armLZ: -0.24,
    armRZ: 2.1,
    armX: -0.12,
    float: 0.8,
    bounce: 0.06,
    lean: 0.015,
    chest: 2.1,
    ear: 2.5,
    color: '#ffca6a',
    wave: true,
  },
  sad: {
    eyeOpen: 0.74,
    eyeCurve: -0.82,
    mouthCurve: -0.85,
    headTiltX: 0.18,
    headTiltZ: 0.07,
    armLZ: -0.035,
    armRZ: 0.035,
    armX: 0.18,
    float: 0.22,
    bounce: 0,
    lean: -0.02,
    chest: 0.35,
    ear: 0.3,
    color: '#dc9258',
    wave: false,
  },
  error: {
    eyeOpen: 0.82,
    eyeCurve: -0.42,
    mouthCurve: -0.6,
    headTiltX: 0.06,
    headTiltZ: -0.035,
    armLZ: -0.05,
    armRZ: 0.05,
    armX: 0.2,
    float: 0.2,
    bounce: 0,
    lean: 0,
    chest: 2.3,
    ear: 2.4,
    color: '#ff5a45',
    wave: false,
  },
  success: {
    eyeOpen: 0.92,
    eyeCurve: 1,
    mouthCurve: 0.95,
    headTiltX: -0.03,
    headTiltZ: -0.03,
    armLZ: -2.45,
    armRZ: 2.45,
    armX: -0.18,
    float: 1.2,
    bounce: 0.075,
    lean: 0,
    chest: 2.2,
    ear: 2.8,
    color: '#baff72',
    wave: false,
  },
};

const damp = THREE.MathUtils.damp;
const clamp = THREE.MathUtils.clamp;

const ORANGE = '#ff7a12';
const DARK = '#1b1410';

export function RobotModel({
  state,
  levelRef,
}: {
  state: RobotState;
  levelRef: { current: number };
}) {
  const root = React.useRef<THREE.Group>(null!);
  const head = React.useRef<THREE.Group>(null!);
  const body = React.useRef<THREE.Group>(null!);
  const leftArm = React.useRef<THREE.Group>(null!);
  const rightArm = React.useRef<THREE.Group>(null!);
  const antenna = React.useRef<THREE.Group>(null!);
  const thought = React.useRef<THREE.Group>(null!);
  const chestMaterial = React.useRef<THREE.MeshStandardMaterial>(null!);
  const chestCore = React.useRef<THREE.MeshBasicMaterial>(null!);
  const earMaterialL = React.useRef<THREE.MeshStandardMaterial>(null!);
  const earMaterialR = React.useRef<THREE.MeshStandardMaterial>(null!);
  const antennaMaterial = React.useRef<THREE.MeshStandardMaterial>(null!);
  const chestLight = React.useRef<THREE.PointLight>(null!);
  const earLightL = React.useRef<THREE.PointLight>(null!);
  const earLightR = React.useRef<THREE.PointLight>(null!);
  const antennaLight = React.useRef<THREE.PointLight>(null!);

  const face = React.useMemo(() => createFaceFrame(), []);
  const pointer = useThree((three) => three.pointer);
  const nextBlink = React.useRef(2.2);
  const blinkStarted = React.useRef(-1);
  const reducedMotion = React.useRef(false);
  const moodColor = React.useMemo(() => new THREE.Color(MOODS.idle.color), []);
  const targetColor = React.useMemo(() => new THREE.Color(), []);

  React.useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => {
      reducedMotion.current = query.matches;
    };
    update();
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);

  useFrame(({ clock }, delta) => {
    const t = clock.elapsedTime;
    const dt = Math.min(delta, 0.05);
    const mood = MOODS[state];
    const energy = clamp(levelRef.current, 0, 1);
    const motion = reducedMotion.current ? 0.18 : 1;

    // Natural blink schedule; timing varies, the lids themselves are smooth.
    if (blinkStarted.current < 0 && t > nextBlink.current) {
      blinkStarted.current = t;
    }
    let blink = 1;
    if (blinkStarted.current >= 0) {
      const progress = (t - blinkStarted.current) / 0.16;
      if (progress >= 1) {
        blinkStarted.current = -1;
        nextBlink.current = t + 2.4 + Math.random() * 3.6;
      } else {
        blink = 1 - Math.sin(progress * Math.PI);
      }
    }

    const bounce =
      mood.bounce > 0
        ? Math.abs(Math.sin(t * (state === 'success' ? 5.4 : 7.2))) *
          mood.bounce *
          motion
        : 0;
    const shake = state === 'error' ? Math.sin(t * 27) * 0.025 : 0;

    root.current.position.y =
      Math.sin(t * 1.18) * 0.045 * mood.float * motion + bounce;
    root.current.position.x = Math.sin(t * 0.72) * 0.012 * mood.float * motion;
    root.current.rotation.z = damp(
      root.current.rotation.z,
      mood.lean + shake,
      5,
      dt,
    );
    root.current.rotation.y = damp(
      root.current.rotation.y,
      pointer.x * 0.055,
      3,
      dt,
    );

    head.current.rotation.x = damp(
      head.current.rotation.x,
      mood.headTiltX + Math.sin(t * 1.05) * 0.012,
      5,
      dt,
    );
    head.current.rotation.z = damp(
      head.current.rotation.z,
      mood.headTiltZ + Math.sin(t * 0.78) * 0.018,
      5,
      dt,
    );
    head.current.rotation.y = damp(
      head.current.rotation.y,
      pointer.x * 0.16 + Math.sin(t * 0.55) * 0.015,
      4,
      dt,
    );
    head.current.position.y =
      1.42 + Math.sin(t * 1.1) * 0.012 * mood.float;

    body.current.scale.y = 1 + Math.sin(t * 2.05) * 0.012;
    body.current.scale.x = 1 - Math.sin(t * 2.05) * 0.006;
    body.current.scale.z = 1 + Math.sin(t * 2.05) * 0.008;

    const rightLift =
      mood.armRZ +
      (mood.wave ? Math.sin(t * 10.5) * 0.24 * (reducedMotion.current ? 0.45 : 1) : 0) +
      (state === 'speaking' ? Math.sin(t * 2.1) * 0.13 * motion : 0);
    rightArm.current.rotation.z = damp(
      rightArm.current.rotation.z,
      rightLift,
      7,
      dt,
    );
    rightArm.current.rotation.x = damp(
      rightArm.current.rotation.x,
      mood.armX,
      5,
      dt,
    );
    leftArm.current.rotation.z = damp(
      leftArm.current.rotation.z,
      mood.armLZ - (state === 'speaking' ? Math.sin(t * 1.8) * 0.05 : 0),
      7,
      dt,
    );
    leftArm.current.rotation.x = damp(
      leftArm.current.rotation.x,
      mood.armX * 0.7,
      5,
      dt,
    );

    antenna.current.rotation.z =
      Math.sin(t * 2.8) * 0.085 + mood.headTiltZ * 0.35;
    antenna.current.rotation.x = Math.sin(t * 2.1 + 0.8) * 0.05;

    targetColor.set(mood.color);
    moodColor.lerp(targetColor, 1 - Math.exp(-6 * dt));

    const speakingMouth =
      state === 'speaking' ? 0.14 + energy * 0.86 : 0;
    face.eyeOpen = damp(
      face.eyeOpen,
      Math.max(0.04, mood.eyeOpen * blink),
      9,
      dt,
    );
    face.eyeCurve = damp(face.eyeCurve, mood.eyeCurve, 6, dt);
    face.mouthCurve = damp(face.mouthCurve, mood.mouthCurve, 6, dt);
    face.mouthOpen = damp(
      face.mouthOpen,
      state === 'speaking' ? speakingMouth : state === 'listening' ? 0.02 : 0,
      12,
      dt,
    );
    face.gazeX = damp(
      face.gazeX,
      state === 'thinking'
        ? 8 + Math.sin(t * 0.9) * 4
        : state === 'error'
          ? Math.sin(t * 14) * 7
          : state === 'speaking'
            ? Math.sin(t * 1.7) * 4
            : pointer.x * 9,
      5,
      dt,
    );
    face.gazeY = damp(
      face.gazeY,
      state === 'thinking'
        ? -8
        : state === 'sad'
          ? 8
          : state === 'happy' || state === 'success'
            ? -3
            : pointer.y * -5,
      5,
      dt,
    );
    face.color.copy(moodColor);

    const chestPulse = 1 + Math.sin(t * 2.3) * 0.09;
    chestMaterial.current.emissiveIntensity = damp(
      chestMaterial.current.emissiveIntensity,
      mood.chest * chestPulse,
      5,
      dt,
    );
    chestMaterial.current.emissive.copy(moodColor);
    chestMaterial.current.color.copy(moodColor);
    chestCore.current.color.copy(moodColor);
    chestLight.current.color.copy(moodColor);
    chestLight.current.intensity = damp(
      chestLight.current.intensity,
      mood.chest * 0.55,
      5,
      dt,
    );

    const earTarget = mood.ear + (state === 'listening' ? energy * 2.6 : 0);
    for (const material of [earMaterialL.current, earMaterialR.current]) {
      material.emissiveIntensity = damp(
        material.emissiveIntensity,
        earTarget,
        6,
        dt,
      );
      material.color.copy(moodColor);
      material.emissive.copy(moodColor);
    }
    for (const light of [earLightL.current, earLightR.current]) {
      light.color.copy(moodColor);
      light.intensity = damp(light.intensity, earTarget * 0.35, 6, dt);
    }

    antennaMaterial.current.emissiveIntensity = damp(
      antennaMaterial.current.emissiveIntensity,
      1.6 + Math.sin(t * 2.4) * 0.35 + (state === 'listening' ? energy * 2 : 0),
      6,
      dt,
    );
    antennaMaterial.current.emissive.copy(moodColor);
    antennaMaterial.current.color.copy(moodColor);
    antennaLight.current.color.copy(moodColor);
    antennaLight.current.intensity = damp(
      antennaLight.current.intensity,
      1.1 + (state === 'listening' ? energy * 1.8 : 0),
      5,
      dt,
    );

    const thoughtTarget = state === 'thinking' ? 1 : 0;
    const thoughtScale = damp(
      thought.current.scale.x,
      thoughtTarget,
      4,
      dt,
    );
    thought.current.scale.setScalar(thoughtScale);
    thought.current.visible = thoughtScale > 0.03;
    thought.current.rotation.y = t * 0.7;
  });

  return (
    <group ref={root}>
      {/* Head */}
      <group ref={head} position={[0, 1.42, 0]}>
        <RoundedBox args={[2.4, 1.6, 1.2]} radius={0.49} smoothness={6} castShadow receiveShadow>
          <meshPhysicalMaterial
            color={ORANGE}
            roughness={0.26}
            metalness={0.18}
            clearcoat={1}
            clearcoatRoughness={0.16}
            emissive="#ff4d00"
            emissiveIntensity={0.055}
            envMapIntensity={0.75}
          />
        </RoundedBox>

        {/* OLED bezel + screen */}
        <RoundedBox
          args={[2.08, 1.24, 0.12]}
          radius={0.28}
          smoothness={5}
          position={[0, 0, 0.56]}
        >
          <meshPhysicalMaterial
            color="#0a0807"
            roughness={0.12}
            metalness={0.45}
            clearcoat={1}
            clearcoatRoughness={0.08}
          />
        </RoundedBox>
        <group position={[0, 0, 0.625]}>
          <RobotFace frame={face} />
        </group>

        {/* Ears */}
        {([-1, 1] as const).map((side) => (
          <group key={side} position={[side * 1.27, 0, 0.02]}>
            <mesh rotation={[0, 0, Math.PI / 2]} castShadow>
              <cylinderGeometry args={[0.34, 0.34, 0.24, 36]} />
              <meshPhysicalMaterial
                color={ORANGE}
                roughness={0.3}
                metalness={0.2}
                clearcoat={1}
                clearcoatRoughness={0.2}
              />
            </mesh>
            <mesh position={[side * 0.14, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
              <cylinderGeometry args={[0.25, 0.25, 0.06, 32]} />
              <meshStandardMaterial color={DARK} roughness={0.4} metalness={0.65} />
            </mesh>
            <mesh position={[side * 0.175, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
              <torusGeometry args={[0.2, 0.026, 12, 48]} />
              <meshStandardMaterial
                ref={side === -1 ? earMaterialL : earMaterialR}
                color="#ffb45c"
                emissive="#ff9a2e"
                emissiveIntensity={0.8}
                toneMapped={false}
              />
            </mesh>
            <pointLight
              ref={side === -1 ? earLightL : earLightR}
              position={[side * 0.3, 0, 0.1]}
              intensity={0.3}
              distance={1.2}
              color="#ff9a2e"
            />
          </group>
        ))}

        {/* Antenna */}
        <group ref={antenna} position={[0, 0.79, 0]}>
          <mesh position={[0, 0.02, 0]}>
            <cylinderGeometry args={[0.085, 0.11, 0.08, 24]} />
            <meshStandardMaterial color={DARK} roughness={0.35} metalness={0.7} />
          </mesh>
          <mesh position={[0, 0.24, 0]}>
            <cylinderGeometry args={[0.026, 0.032, 0.4, 16]} />
            <meshStandardMaterial color="#342219" roughness={0.4} metalness={0.6} />
          </mesh>
          <mesh position={[0, 0.49, 0]} castShadow>
            <sphereGeometry args={[0.165, 32, 24]} />
            <meshStandardMaterial
              ref={antennaMaterial}
              color="#ffb45c"
              emissive="#ff8a1f"
              emissiveIntensity={1.8}
              toneMapped={false}
            />
          </mesh>
          <pointLight
            ref={antennaLight}
            position={[0, 0.49, 0]}
            intensity={1.1}
            distance={2.2}
            color="#ffae42"
          />
        </group>
      </group>

      {/* Neck */}
      <mesh position={[0, 0.56, 0]}>
        <cylinderGeometry args={[0.29, 0.33, 0.24, 28]} />
        <meshStandardMaterial color={DARK} roughness={0.42} metalness={0.65} />
      </mesh>

      {/* Torso */}
      <group ref={body} position={[0, -0.12, 0]}>
        <RoundedBox args={[1.66, 1.36, 1.05]} radius={0.43} smoothness={6} castShadow receiveShadow>
          <meshPhysicalMaterial
            color={ORANGE}
            roughness={0.27}
            metalness={0.18}
            clearcoat={1}
            clearcoatRoughness={0.17}
            emissive="#ff4d00"
            emissiveIntensity={0.05}
            envMapIntensity={0.7}
          />
        </RoundedBox>

        {/* Chest indicator */}
        <group position={[0, 0.13, 0.53]}>
          <mesh>
            <torusGeometry args={[0.19, 0.047, 16, 48]} />
            <meshStandardMaterial
              ref={chestMaterial}
              color="#ffb45c"
              emissive="#ff9a2e"
              emissiveIntensity={1.2}
              roughness={0.25}
              metalness={0.3}
              toneMapped={false}
            />
          </mesh>
          <mesh position={[0, 0, -0.01]}>
            <circleGeometry args={[0.155, 40]} />
            <meshBasicMaterial ref={chestCore} color="#ffb45c" toneMapped={false} />
          </mesh>
          <pointLight
            ref={chestLight}
            position={[0, 0, 0.14]}
            intensity={0.5}
            distance={1.6}
            color="#ff9a2e"
          />
        </group>

        {/* Waist seam */}
        <mesh position={[0, -0.67, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.62, 0.055, 12, 56]} />
          <meshStandardMaterial color={DARK} roughness={0.4} metalness={0.6} />
        </mesh>
      </group>

      {/* Arms */}
      <group ref={rightArm} position={[1.0, 0.3, 0]} rotation={[0, 0, 0.12]}>
        <mesh castShadow>
          <sphereGeometry args={[0.26, 28, 20]} />
          <meshPhysicalMaterial
            color={ORANGE}
            roughness={0.28}
            metalness={0.2}
            clearcoat={0.9}
          />
        </mesh>
        <mesh position={[0, -0.03, 0]} rotation={[0, 0, Math.PI / 2]}>
          <torusGeometry args={[0.205, 0.036, 12, 40]} />
          <meshStandardMaterial color={DARK} roughness={0.4} metalness={0.65} />
        </mesh>
        <mesh position={[0, -0.33, 0]} castShadow>
          <capsuleGeometry args={[0.165, 0.24, 8, 24]} />
          <meshPhysicalMaterial
            color={ORANGE}
            roughness={0.3}
            metalness={0.18}
            clearcoat={0.85}
          />
        </mesh>
        <mesh position={[0, -0.67, 0.02]} castShadow>
          <sphereGeometry args={[0.215, 26, 20]} />
          <meshStandardMaterial color={DARK} roughness={0.4} metalness={0.58} />
        </mesh>
      </group>

      <group ref={leftArm} position={[-1.0, 0.3, 0]} rotation={[0, 0, -0.12]}>
        <mesh castShadow>
          <sphereGeometry args={[0.26, 28, 20]} />
          <meshPhysicalMaterial
            color={ORANGE}
            roughness={0.28}
            metalness={0.2}
            clearcoat={0.9}
          />
        </mesh>
        <mesh position={[0, -0.03, 0]} rotation={[0, 0, Math.PI / 2]}>
          <torusGeometry args={[0.205, 0.036, 12, 40]} />
          <meshStandardMaterial color={DARK} roughness={0.4} metalness={0.65} />
        </mesh>
        <mesh position={[0, -0.33, 0]} castShadow>
          <capsuleGeometry args={[0.165, 0.24, 8, 24]} />
          <meshPhysicalMaterial
            color={ORANGE}
            roughness={0.3}
            metalness={0.18}
            clearcoat={0.85}
          />
        </mesh>
        <mesh position={[0, -0.67, 0.02]} castShadow>
          <sphereGeometry args={[0.215, 26, 20]} />
          <meshStandardMaterial color={DARK} roughness={0.4} metalness={0.58} />
        </mesh>
      </group>

      {/* Legs */}
      {([-1, 1] as const).map((side) => (
        <group key={side} position={[side * 0.4, -0.82, 0]}>
          <mesh position={[0, -0.13, 0]} castShadow>
            <capsuleGeometry args={[0.17, 0.16, 8, 22]} />
            <meshPhysicalMaterial
              color={ORANGE}
              roughness={0.3}
              metalness={0.18}
              clearcoat={0.8}
            />
          </mesh>
          <mesh position={[0, -0.32, 0]} rotation={[Math.PI / 2, 0, 0]}>
            <torusGeometry args={[0.17, 0.038, 10, 32]} />
            <meshStandardMaterial color={DARK} roughness={0.42} metalness={0.6} />
          </mesh>
          <RoundedBox
            args={[0.56, 0.26, 0.76]}
            radius={0.12}
            smoothness={4}
            position={[0, -0.43, 0.12]}
            castShadow
          >
            <meshStandardMaterial color={DARK} roughness={0.38} metalness={0.58} />
          </RoundedBox>
        </group>
      ))}

      {/* Thinking indicators */}
      <group ref={thought} position={[0, 1.45, 0]} scale={0} visible={false}>
        <mesh rotation={[1.18, 0, 0.15]}>
          <torusGeometry args={[1.08, 0.012, 8, 96, Math.PI * 1.35]} />
          <meshBasicMaterial color="#ffb45c" transparent opacity={0.55} toneMapped={false} />
        </mesh>
        {[0, 1, 2].map((index) => {
          const angle = (index / 3) * Math.PI * 2;
          return (
            <mesh
              key={index}
              position={[Math.cos(angle) * 1.08, Math.sin(angle * 2) * 0.18, Math.sin(angle) * 0.55]}
            >
              <sphereGeometry args={[0.045 + index * 0.012, 12, 10]} />
              <meshBasicMaterial color="#ffd08a" toneMapped={false} />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}
