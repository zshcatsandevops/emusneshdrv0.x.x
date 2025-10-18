#!/usr/bin/env python3
"""
EmuSNES v0.2 — Super Nintendo Entertainment System Emulator
Based on SNES9x v0.1 Original Architecture

© 2025 Samsoft Interactive
Developed for educational and preservation purposes only.
Super FX™ is a trademark of Nintendo.
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import time
import os

# --- EmuSNES Core Engine ---
class EmuSNESCore:
    SNES_CLOCK_SPEED = 3579545
    SNES_REFRESH_RATE = 60.0988475
    CYCLES_PER_SCANLINE = 1364
    SCANLINES_PER_FRAME = 262

    def __init__(self, screen_update_callback, status_callback):
        self.screen_update_callback = screen_update_callback
        self.status_callback = status_callback

        self.memory = self.MemoryMap()
        self.cpu = self.CPU65C816(self.memory)
        self.ppu = self.PPU(self.memory, self.screen_update_callback)
        self.apu = self.APU(self.memory)
        self.dsp = self.DSP()

        self.running = False
        self.paused = False
        self.frame_skip = 0
        self.fps_counter = 0
        self.last_fps_time = time.time()

        self.rom_name = ""
        self.rom_type = "LoROM"
        self.rom_size = 0
        self.rom_header_info = {}

    def load_rom(self, rom_path):
        try:
            with open(rom_path, "rb") as f:
                rom_data = f.read()
            if len(rom_data) % 1024 == 512:
                self.status_callback("Detected copier header, removing…")
                rom_data = rom_data[512:]
            self.rom_size = len(rom_data)
            self.rom_name = os.path.basename(rom_path)
            self.rom_type = "LoROM" if self.rom_size <= 0x200000 else "HiROM"
            self.memory.load_rom(rom_data, self.rom_type)
            self._parse_header(rom_data)
            info = f"{self.rom_name} ({self.rom_type}, {self.rom_size//1024} KB)"
            if self.rom_header_info.get('type_name'):
                info += f" - {self.rom_header_info['type_name']}"
            self.status_callback(f"Loaded: {info}")
            return True
        except Exception as e:
            self.status_callback(f"ROM Load Error: {e}")
            return False

    def _parse_header(self, rom_data):
        header_offset = 0x7FC0 if self.rom_type == "LoROM" else 0xFFC0
        if header_offset + 32 <= len(rom_data):
            try:
                title_bytes = rom_data[header_offset:header_offset+21]
                self.rom_name = title_bytes.decode('ascii', errors='ignore').strip()
                rom_type_byte = rom_data[header_offset + 0x15]
                rom_size_byte = rom_data[header_offset + 0x16]
                rom_types = {0x00: "ROM Only", 0x01: "ROM+RAM", 0x02: "ROM+RAM+BATT"}
                self.rom_header_info['type_name'] = rom_types.get(rom_type_byte & 0x0F, "Unknown Type")
                self.rom_header_info['rom_size_kb'] = 1 << rom_size_byte
            except Exception:
                self.rom_header_info = {}

    def reset(self):
        self.cpu.reset()
        self.ppu.reset()
        self.apu.reset()
        self.dsp.reset()
        self.memory.reset()
        self.status_callback("System Reset")

    def run_frame(self):
        if not self.running: return
        if self.paused:
            time.sleep(1.0 / 60.0)
            return
            
        start_time = time.time()
        cycles_target = self.CYCLES_PER_SCANLINE * self.SCANLINES_PER_FRAME
        cycles_executed = 0
        
        try:
            while cycles_executed < cycles_target and self.running:
                cycles = self.cpu.step()
                if cycles == 0:
                    self.running = False
                    self.status_callback("CPU Halted - Unknown Opcode")
                    break
                cycles_executed += cycles
                if cycles_executed % self.CYCLES_PER_SCANLINE == 0:
                    self.ppu.update_scanline(cycles_executed // self.CYCLES_PER_SCANLINE)
                if cycles_executed % 64 == 0:
                    self.apu.update()
        except Exception as e:
            self.status_callback(f"Emulation Error: {e}")
            self.running = False
            return

        self.ppu.render_frame()
        self.fps_counter += 1
        now = time.time()
        if now - self.last_fps_time >= 1.0:
            fps = self.fps_counter / (now - self.last_fps_time)
            self.status_callback(f"FPS: {fps:.1f} | {self.rom_name}")
            self.fps_counter = 0
            self.last_fps_time = now
        
        frame_time = time.time() - start_time
        target = 1.0 / self.SNES_REFRESH_RATE
        if frame_time < target:
            time.sleep(target - frame_time)

    class CPU65C816:
        def __init__(self, memory):
            self.memory = memory
            self.a = self.x = self.y = 0
            self.s = 0x01FF
            self.d = self.db = self.pb = 0
            self.pc = 0
            self.p = 0x34
            self.e = 1

        def reset(self):
            self.e = 1; self.pb = 0; self.db = 0
            self.pc = (self.memory.read(0xFFFD) << 8) | self.memory.read(0xFFFC)
            self.s = 0x01FF; self.p = 0x34

        def step(self):
            opcode = self.memory.read((self.pb << 16) | self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
            cycles = 2
            
            if opcode == 0xEA:   # NOP
                cycles = 2
            elif opcode == 0xA9: # LDA #imm
                self.a = self.memory.read((self.pb << 16) | self.pc)
                self.pc = (self.pc + 1) & 0xFFFF
                self._set_nz(self.a); cycles = 2
            elif opcode == 0xE8: # INX
                self.x = (self.x + 1) & 0xFF; self._set_nz(self.x); cycles = 2
            elif opcode == 0xC8: # INY
                self.y = (self.y + 1) & 0xFF; self._set_nz(self.y); cycles = 2
            elif opcode == 0x4C: # JMP abs
                low = self.memory.read((self.pb << 16) | self.pc)
                self.pc = (self.pc + 1) & 0xFFFF
                high = self.memory.read((self.pb << 16) | self.pc)
                self.pc = (high << 8) | low; cycles = 3
            else: return 0
            return cycles

        def _set_nz(self, v):
            self.p = (self.p | 0x02) if v == 0 else (self.p & ~0x02)
            self.p = (self.p | 0x80) if v & 0x80 else (self.p & ~0x80)

    class MemoryMap:
        def __init__(self):
            self.wram = bytearray(128 * 1024)
            self.vram = bytearray(64 * 1024)
            self.rom = bytearray(4 * 1024 * 1024)
            self.rom_type = "LoROM"

        def reset(self):
            self.wram[:] = b"\0" * len(self.wram)
            self.vram[:] = b"\0" * len(self.vram)

        def load_rom(self, rom_data, rom_type):
            self.rom_type = rom_type
            size = min(len(rom_data), len(self.rom))
            self.rom[:size] = rom_data[:size]

        def read(self, addr):
            bank, ofs = (addr >> 16) & 0xFF, addr & 0xFFFF
            if bank < 0x40 and ofs < 0x2000: return self.wram[ofs & 0x1FFF]
            if ofs >= 0x8000:
                rom_addr = ((bank & 0x7F) << 15) | (ofs & 0x7FFF)
                return self.rom[rom_addr % len(self.rom)]
            return 0

        def write(self, addr, val):
            bank, ofs = (addr >> 16) & 0xFF, addr & 0xFFFF
            if bank < 0x40 and ofs < 0x2000: self.wram[ofs & 0x1FFF] = val & 0xFF

    class PPU:
        def __init__(self, memory, screen_update_callback):
            self.memory = memory
            self.screen_update_callback = screen_update_callback
            self.frame_buffer = bytearray(256 * 224 * 3)
            self.brightness = 15

        def reset(self): self.frame_buffer = bytearray(256 * 224 * 3)
        def render_frame(self): self.screen_update_callback(self.frame_buffer)

        def update_scanline(self, y):
            if y < 224:
                for x in range(256):
                    c = (self.memory.vram[(y * 32 + x // 8) % len(self.memory.vram)] + x) % 32
                    scaled_c = int(c * ((self.brightness + 1) / 16.0) * 8)
                    idx = (y * 256 + x) * 3
                    self.frame_buffer[idx:idx+3] = scaled_c, scaled_c, scaled_c

    class APU:
        def __init__(self, mem): self.enabled = True
        def reset(self): self.enabled = True
        def update(self): pass

    class DSP:
        def __init__(self): self.volume = 127
        def reset(self): self.volume = 127

# --- GUI Application ---
class EmuSNESApp:
    def __init__(self, master):
        self.master = master
        self.rom_loaded = False
        master.title("EmuSNES v0.2 — Samsoft Interactive © 2025")
        master.geometry("560x540")
        master.resizable(False, False)
        master.configure(bg="#c0c0c0")

        self.status_frame = tk.Frame(master, bg="#808080", height=25)
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X); self.status_frame.pack_propagate(False)
        self.status_label = tk.Label(self.status_frame, text="EmuSNES v0.2 Ready", bg="#808080", fg="white", font=("MS Sans Serif", 8), anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, padx=5, pady=2)

        self.menu_bar = tk.Menu(master); master.config(menu=self.menu_bar)
        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open ROM…", command=self.load_rom, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.exit_emulator, accelerator="Alt+F4")

        self.emu_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Emulation", menu=self.emu_menu)
        self.emu_menu.add_command(label="Pause", command=self.toggle_pause, accelerator="P", state=tk.DISABLED)

        help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About EmuSNES…", command=self.show_about)

        self.screen_frame = tk.Frame(master, bg="black", width=512, height=448, relief=tk.SUNKEN, borderwidth=2)
        self.screen_frame.pack(expand=True, padx=20, pady=20); self.screen_frame.pack_propagate(False)
        
        self.screen_photo_unscaled = tk.PhotoImage(width=256, height=224)
        self.screen_photo_scaled = self.screen_photo_unscaled.zoom(2)
        self.screen_label = tk.Label(self.screen_frame, image=self.screen_photo_scaled, bg="black")
        self.screen_label.pack()

        self.welcome_label = tk.Label(self.screen_label, text="EmuSNES v0.2\n\nFile > Open ROM… to begin", compound=tk.CENTER, bg="black", fg="white", font=("Arial", 14, "bold"))
        self.welcome_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        
        self.emulator = EmuSNESCore(self.update_screen, self.update_status)
        self.emulation_thread = None

        master.bind("<Control-o>", lambda e: self.load_rom())
        master.bind("<p>", lambda e: self.toggle_pause() if self.rom_loaded else None)
        master.protocol("WM_DELETE_WINDOW", self.exit_emulator)

    def update_status(self, msg): self.status_label.config(text=f"EmuSNES v0.2 — {msg}")

    def update_screen(self, frame_buffer):
        self.master.after(0, self._update_screen_on_main_thread, frame_buffer)

    def _update_screen_on_main_thread(self, frame_buffer):
        if not self.master.winfo_exists(): return
        lines = ('{' + ' '.join(f'#{frame_buffer[i]:02x}{frame_buffer[i+1]:02x}{frame_buffer[i+2]:02x}' for i in range(y*768, y*768+768, 3)) + '}' for y in range(224))
        self.screen_photo_unscaled.put(' '.join(lines))
        self.screen_photo_scaled = self.screen_photo_unscaled.zoom(2)
        self.screen_label.config(image=self.screen_photo_scaled)

    def load_rom(self):
        if self.emulator.running: self.stop_emulation()
        fp = filedialog.askopenfilename(title="Open SNES ROM", filetypes=[("SNES ROM Files","*.smc *.sfc *.swc *.fig"),("All Files","*.*")])
        if fp and self.emulator.load_rom(fp):
            self.rom_loaded = True
            if self.welcome_label:
                self.welcome_label.destroy()
                self.welcome_label = None
            self.emulator.reset()
            self.start_emulation()
            self.emu_menu.entryconfig("Pause", state=tk.NORMAL)

    def start_emulation(self):
        if self.emulator.running: return
        self.emulator.running = True
        self.emulation_thread = threading.Thread(target=self.emulation_loop, daemon=True)
        self.emulation_thread.start()

    def stop_emulation(self):
        self.emulator.running = False
        if self.emulation_thread:
            self.emulation_thread.join(timeout=1.0)

    def toggle_pause(self):
        if not self.rom_loaded: return
        self.emulator.paused = not self.emulator.paused
        if self.emulator.paused:
            self.update_status(f"Paused | {self.emulator.rom_name}")
            self.emu_menu.entryconfig("Pause", label="Resume")
        else:
            self.update_status(f"Resumed | {self.emulator.rom_name}")
            self.emu_menu.entryconfig("Pause", label="Pause")

    def emulation_loop(self):
        while self.emulator.running: self.emulator.run_frame()

    def exit_emulator(self):
        self.stop_emulation()
        self.master.quit()

    def show_about(self):
        messagebox.showinfo("About EmuSNES", "EmuSNES v0.2 — Samsoft Interactive © 2025\nSuper Nintendo Entertainment System Emulator\n\nEducational build for historical preservation only.")

if __name__ == "__main__":
    root = tk.Tk()
    app = EmuSNESApp(root)
    root.mainloop()
