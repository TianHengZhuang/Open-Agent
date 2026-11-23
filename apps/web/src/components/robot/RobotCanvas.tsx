'use client';

import * as React from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { ContactShadows } from '@react-three/drei';
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';

import type { RobotState } from '@/features/robot/robot-machine';
import { RobotModel } from './RobotModel';

/** Local procedural reflections: no CDN HDR, no network dependency. */
function StudioEnvironment() {
  const gl = useThree((three) => three.gl);
  const scene = useThree((three) => three.scene);

  React.useEffect(() => {
    const generator = new THREE.PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const target = generator.fromScene(room, 0.04);
    scene.environment = target.texture;
    scene.environmentIntensity = 0.55;
    return () => {
      scene.environment = null;
      target.dispose();
      generator.dispose();
      (room as unknown as { dispose?: () => void }).dispose?.();
    };
  }, [gl, scene]);

  return null;
}

function CameraRig() {
  const camera = useThree((three) => three.camera);
  const size = useThree((three) => three.size);

  React.useEffect(() => {
    // A narrow companion rail is much taller than it is wide. Fit the whole
    // character horizontally first, then guarantee head-room vertically.
    const aspect = Math.max(0.35, size.width / Math.max(1, size.height));
    const fov = THREE.MathUtils.degToRad(32);
    const fitHeight = 4.9 / (2 * Math.tan(fov / 2));
    const fitWidth = 2.9 / (2 * Math.tan(fov / 2) * aspect);
    const distance = Math.max(fitHeight, fitWidth);
    camera.position.set(0, 0.22, distance);
    camera.lookAt(0, 0.02, 0);
    camera.updateProjectionMatrix();
  }, [camera, size.height, size.width]);

  return null;
}

/**
 * Real-energy equalizer ring around the feet. Microphone RMS and synthesized
 * speech energy both feed the same render loop.
 */
function VoiceRing({
  state,
  levelRef,
}: {
  state: RobotState;
  levelRef: { current: number };
}) {
  const bars = 40;
  const mesh = React.useRef<THREE.InstancedMesh>(null!);
  const dummy = React.useMemo(() => new THREE.Object3D(), []);

  useFrame(({ clock }) => {
    const active = state === 'listening' || state === 'speaking';
    const instance = mesh.current;
    if (!instance) return;
    instance.visible = active;
    if (!active) return;

    const t = clock.elapsedTime;
    const energy = THREE.MathUtils.clamp(levelRef.current, 0, 1);
    for (let index = 0; index < bars; index += 1) {
      const angle = (index / bars) * Math.PI * 2;
      const wave = Math.abs(Math.sin(t * 4.2 + index * 0.58));
      const height = 0.035 + energy * (0.18 + wave * 0.72);
      dummy.position.set(
        Math.cos(angle) * 2.02,
        -1.78 + height / 2,
        Math.sin(angle) * 0.62,
      );
      dummy.rotation.set(0, 0, 0);
      dummy.scale.set(1, height, 1);
      dummy.updateMatrix();
      instance.setMatrixAt(index, dummy.matrix);
    }
    instance.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, bars]} frustumCulled={false} visible={false}>
      <boxGeometry args={[0.026, 1, 0.026]} />
      <meshBasicMaterial color="#ff9a36" transparent opacity={0.72} toneMapped={false} />
    </instancedMesh>
  );
}

export function RobotCanvas({
  state,
  levelRef,
}: {
  state: RobotState;
  levelRef: { current: number };
}) {
  return (
    <Canvas
      shadows
      dpr={[1, 1.8]}
      camera={{ position: [0, 0.22, 8.35], fov: 32, near: 0.1, far: 40 }}
      gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.ACESFilmicToneMapping;
        gl.toneMappingExposure = 1.04;
      }}
      style={{ touchAction: 'pan-y' }}
    >
      <StudioEnvironment />
      <CameraRig />

      <ambientLight intensity={0.65} color="#ffe8d4" />
      <hemisphereLight args={['#ffd7b0', '#160b06', 0.8]} />
      <directionalLight
        castShadow
        position={[3.4, 5.2, 4.2]}
        intensity={2.7}
        color="#ffe1c2"
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
        shadow-bias={-0.0004}
      />
      <pointLight position={[0, 1.8, 3.2]} intensity={18} distance={9} color="#ff9a44" />
      <pointLight position={[-3.5, 1.2, 1.5]} intensity={9} distance={7} color="#ff6a12" />
      <pointLight position={[3.2, 2.4, -2.8]} intensity={14} distance={8} color="#ff4d00" />

      <group position={[0, -0.52, 0]}>
        <RobotModel state={state} levelRef={levelRef} />
      </group>

      <VoiceRing state={state} levelRef={levelRef} />

      {/* Warm pool and contact shadow keep the character grounded. */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -1.94, 0]}>
        <circleGeometry args={[2.6, 64]} />
        <meshBasicMaterial color="#ff6a12" transparent opacity={0.05} depthWrite={false} />
      </mesh>
      <ContactShadows
        position={[0, -1.93, 0]}
        opacity={0.48}
        scale={5.4}
        blur={2.6}
        far={2.8}
        resolution={512}
        color="#2a0c00"
      />
    </Canvas>
  );
}
