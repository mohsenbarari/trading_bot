<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import * as THREE from 'three';

const canvasRef = ref<HTMLCanvasElement | null>(null);

let renderer: THREE.WebGLRenderer;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let torus: THREE.Mesh;
let animationFrameId: number;

onMounted(() => {
  if (!canvasRef.value) return;
  const canvas = canvasRef.value;
  
  // 1. Scene
  scene = new THREE.Scene();

  // 2. Camera
  camera = new THREE.PerspectiveCamera(75, canvas.clientWidth / canvas.clientHeight, 0.1, 1000);
  camera.position.z = 5;

  // 3. Renderer
  renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
  renderer.setSize(canvas.clientWidth, canvas.clientHeight);
  renderer.setPixelRatio(window.devicePixelRatio);
  
  // 4. Object
  const geometry = new THREE.TorusKnotGeometry(1.5, 0.3, 100, 16);
  const material = new THREE.MeshStandardMaterial({
    color: 0x007AFF,
    roughness: 0.4,
    metalness: 0.6,
  });
  torus = new THREE.Mesh(geometry, material);
  scene.add(torus);

  // 5. Lights
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
  scene.add(ambientLight);
  const pointLight = new THREE.PointLight(0xffffff, 1);
  pointLight.position.set(5, 5, 5);
  scene.add(pointLight);

  // Handle Resize
  const resizeObserver = new ResizeObserver(entries => {
      const entry = entries[0];
      const { width, height } = entry.contentRect;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
  });
  if (canvas.parentElement) {
    resizeObserver.observe(canvas.parentElement);
  }

  // Animation Loop
  const animate = () => {
    animationFrameId = requestAnimationFrame(animate);
    torus.rotation.x += 0.005;
    torus.rotation.y += 0.007;
    renderer.render(scene, camera);
  };
  animate();
});

onUnmounted(() => {
  cancelAnimationFrame(animationFrameId);
  renderer.dispose();
  // Clean up other resources if necessary
});
</script>

<template>
  <div class="animation-container">
    <canvas ref="canvasRef"></canvas>
    <div class="overlay-text">
      <h2>به پنل کاربری خوش آمدید</h2>
      <p>از منوی پایین برای مدیریت حساب خود استفاده کنید.</p>
    </div>
  </div>
</template>

<style scoped>
.animation-container {
  width: 100%;
  height: 100%;
  position: relative;
  display: flex;
  justify-content: center;
  align-items: center;
  border-radius: 16px;
  overflow: hidden;
}
canvas {
  width: 100%;
  height: 100%;
  display: block;
  position: absolute;
  top: 0;
  left: 0;
}
.overlay-text {
  position: relative;
  z-index: 1;
  text-align: center;
  color: var(--text-color);
  background-color: rgba(255, 255, 255, 0.7);
  padding: 20px 30px;
  border-radius: 12px;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.overlay-text h2 {
  margin: 0 0 8px 0;
  font-size: 20px;
}
.overlay-text p {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
}
</style>

