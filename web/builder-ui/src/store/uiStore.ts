import { create } from 'zustand'

interface UiState {
  drawerOpen: boolean
  promptInspectorOpen: boolean
  paletteCollapsed: boolean
  propertiesPaneCollapsed: boolean
  theme: 'dark' | 'light'

  setDrawerOpen(open: boolean): void
  setPromptInspectorOpen(open: boolean): void
  setPaletteCollapsed(v: boolean): void
  setPropertiesPaneCollapsed(v: boolean): void
}

export const useUiStore = create<UiState>((set) => ({
  drawerOpen: false,
  promptInspectorOpen: false,
  paletteCollapsed: false,
  propertiesPaneCollapsed: false,
  theme: 'dark',

  setDrawerOpen: (open) => set({ drawerOpen: open }),
  setPromptInspectorOpen: (open) => set({ promptInspectorOpen: open }),
  setPaletteCollapsed: (v) => set({ paletteCollapsed: v }),
  setPropertiesPaneCollapsed: (v) => set({ propertiesPaneCollapsed: v }),
}))
