import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import HomeAnimation from './HomeAnimation.vue'

const threeMocks = vi.hoisted(() => ({
  renderer: null as any,
  camera: null as any,
  sceneAdd: vi.fn(),
  pointLightPositionSet: vi.fn(),
}))

vi.mock('three', () => {
  class Scene {
    add = threeMocks.sceneAdd
  }

  class PerspectiveCamera {
    aspect = 1
    position = { z: 0 }
    updateProjectionMatrix = vi.fn()
    constructor(..._args: any[]) {
      threeMocks.camera = this
    }
  }

  class WebGLRenderer {
    setSize = vi.fn()
    setPixelRatio = vi.fn()
    render = vi.fn()
    dispose = vi.fn()
    constructor(..._args: any[]) {
      threeMocks.renderer = this
    }
  }

  class Mesh {
    rotation = { x: 0, y: 0 }
    constructor(..._args: any[]) {}
  }

  class TorusKnotGeometry {
    constructor(..._args: any[]) {}
  }

  class MeshStandardMaterial {
    constructor(..._args: any[]) {}
  }

  class AmbientLight {
    constructor(..._args: any[]) {}
  }

  class PointLight {
    position = { set: threeMocks.pointLightPositionSet }
    constructor(..._args: any[]) {}
  }

  return {
    Scene,
    PerspectiveCamera,
    WebGLRenderer,
    Mesh,
    TorusKnotGeometry,
    MeshStandardMaterial,
    AmbientLight,
    PointLight,
  }
})

class ResizeObserverMock {
  static instances: ResizeObserverMock[] = []
  callback: ResizeObserverCallback
  observe = vi.fn()

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    ResizeObserverMock.instances.push(this)
  }
}

describe('HomeAnimation.vue', () => {
  beforeEach(() => {
    threeMocks.renderer = null
    threeMocks.camera = null
    threeMocks.sceneAdd.mockReset()
    threeMocks.pointLightPositionSet.mockReset()
    ResizeObserverMock.instances = []
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 42))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })

  it('initializes the three.js scene, reacts to resize, and disposes resources on unmount', async () => {
    const wrapper = mount(HomeAnimation, {
      attachTo: document.body,
    })

    expect(wrapper.text()).toContain('به پنل کاربری خوش آمدید')
    expect(threeMocks.renderer.setPixelRatio).toHaveBeenCalledWith(window.devicePixelRatio)
    expect(threeMocks.sceneAdd).toHaveBeenCalled()
    expect(ResizeObserverMock.instances[0]?.observe).toHaveBeenCalled()
    expect(globalThis.requestAnimationFrame).toHaveBeenCalled()
    expect(threeMocks.renderer.render).toHaveBeenCalled()

    ResizeObserverMock.instances[0]?.callback([
      {
        contentRect: { width: 320, height: 160 },
      } as ResizeObserverEntry,
    ], {} as ResizeObserver)

    expect(threeMocks.camera.aspect).toBe(2)
    expect(threeMocks.camera.updateProjectionMatrix).toHaveBeenCalled()
    expect(threeMocks.renderer.setSize).toHaveBeenCalledWith(320, 160)

    wrapper.unmount()

    expect(globalThis.cancelAnimationFrame).toHaveBeenCalledWith(42)
    expect(threeMocks.renderer.dispose).toHaveBeenCalled()
  })
})