import tkinter as tk
from tkinter import filedialog
import serial
import threading
import time
import csv

# open the serial port to talk to the Arduino
# you might need to change 'COM7' to match your system
ser = serial.Serial('COM7', 9600, timeout=0.1)

# --- basic flags and default parameters ---
flush_active = False                 # true when water flushing is active
free_reward_enabled = True           # whether free rewards are allowed
habituation_enabled = False          # whether habituation mode is turned on
struggle_threshold = 350.0           # default struggle threshold in grams
fix_duration = 7                     # default fixation duration in seconds
fix_delay = 1                        # how long to wait before another fixation is allowed
escape_buffer = 500                  # time threshold for counting escapes
reward_buffer = 1000                 # time the animal must be fixated before rewards count

# store total counts for the session
totals = {"time": 0, "fix": 0, "escape": 0, "timeup": 0, "struggle": 0, "reward": 0}

# timing control for the session
session_start_time = None            # when the session started
timer_running = False                # whether the timer is currently counting

# this adds a line to the console log box on the GUI
def add_console_log(msg):
    console_box.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    console_box.see(tk.END)

# toggles water flushing mode
def toggle_flush():
    global flush_active
    flush_active = not flush_active
    flush_button.config(text="Stop Flushing" if flush_active else "Flush Water")
    ser.write(b'W' if flush_active else b'w')  # tell Arduino to start/stop flushing
    add_console_log(f"Flush toggled: {'ON' if flush_active else 'OFF'}")

# toggles free reward mode (rewards given even if not fixated)
def toggle_free_reward():
    global free_reward_enabled
    free_reward_enabled = not free_reward_enabled
    ser.write(b'M1' if free_reward_enabled else b'M0')
    add_console_log(f"Free reward {'ENABLED' if free_reward_enabled else 'DISABLED'}")

# toggles habituation mode (after 25 rewards actuator moves back one level)
def toggle_habituation():
    global habituation_enabled
    habituation_enabled = not habituation_enabled
    ser.write(b'H1' if habituation_enabled else b'H0')
    add_console_log(f"Habituation Mode {'ENABLED' if habituation_enabled else 'DISABLED'}")

# helper function for toggling actuator buttons
def toggle_button(button, state_var, command_on, command_off, label):
    state_var[0] = not state_var[0]
    ser.write(command_on if state_var[0] else command_off)
    button.config(bg="#4CAF50" if state_var[0] else "#e0e0e0")
    add_console_log(f"{label} {'START' if state_var[0] else 'STOP'}")

# sends a value to Arduino (for parameters like fix delay, reward buffer, etc.)
# we make sure to send command + value together in one go to avoid bugs
def send_value(entry, command, label, multiplier=1):
    try:
        val = float(entry.get())
        cmd = command.decode()  # convert b'X' to 'X'
        ser.write(f"{cmd}{int(val*multiplier)}\n".encode())
        add_console_log(f"{label} set to {val}")
    except ValueError:
        add_console_log(f"Invalid input for {label}")

# individual helper functions for each parameter
def send_threshold(): send_value(threshold_entry, b'T', "Struggle Threshold")
def send_fix_duration(): send_value(fix_duration_entry, b'X', "Fix Duration", 1000)
def send_fix_delay(): send_value(fix_delay_entry, b'Y', "Fix Delay", 1000)
def send_escape_buffer(): send_value(escape_buffer_entry, b'Z', "Escape Buffer")
def send_reward_buffer(): send_value(reward_buffer_entry, b'Q', "Reward Buffer")

# updates the data table when new trial data is received
def update_table(event_data):
    duration, fix, escape, timeup, struggle, reward = event_data
    for i, val in enumerate(event_data):
        table_labels[2][i].config(text=str(val))
    # add new values to totals
    totals["time"] += duration
    totals["fix"] += fix
    totals["escape"] += escape
    totals["timeup"] += timeup
    totals["struggle"] += struggle
    totals["reward"] += reward
    # update table display
    table_labels[1][0].config(text=f"{totals['time']:.1f}")
    table_labels[1][1].config(text=str(totals["fix"]))
    table_labels[1][2].config(text=str(totals["escape"]))
    table_labels[1][3].config(text=str(totals["timeup"]))
    table_labels[1][4].config(text=str(totals["struggle"]))
    table_labels[1][5].config(text=str(totals["reward"]))

# saves data to a CSV file
def save_data_to_file():
    rat = rat_name_entry.get().strip()
    date_str = time.strftime("%Y-%m-%d")
    default_name = f"{rat}_{date_str}.csv" if rat else f"session_{date_str}.csv"

    file_path = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV files", "*.csv")],
        title="Save Data As",
        initialfile=default_name
    )
    if not file_path:
        return

    with open(file_path, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Time(s)", "Fix", "Escape", "TimeUp", "Struggle", "Reward"])
        writer.writerow([
            table_labels[1][0].cget("text"),
            table_labels[1][1].cget("text"),
            table_labels[1][2].cget("text"),
            table_labels[1][3].cget("text"),
            table_labels[1][4].cget("text"),
            table_labels[1][5].cget("text")
        ])
    add_console_log(f"Data saved to {file_path}")

# updates the session timer every second
def update_session_timer():
    if session_start_time and timer_running:
        elapsed = int(time.time() - session_start_time)
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60
        session_timer_label.config(text=f"Session Time: {hours:02}:{minutes:02}:{seconds:02}")
    root.after(1000, update_session_timer)  # keep calling itself every second

# reads serial messages from Arduino and updates GUI in real-time
def update_serial():
    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue

            # if we get an event line, parse and update the table
            if line.startswith("EVENT,"):
                parts = line.split(",")
                if len(parts) == 7:
                    event_data = [
                        float(parts[1]), int(parts[2]), int(parts[3]),
                        int(parts[4]), int(parts[5]), int(parts[6])
                    ]
                    update_table(event_data)

            # handle various status messages from Arduino
            if "Fixation Engaged" in line:
                fixation_label.config(text="Fixation: ACTIVE", fg="green")
                add_console_log("Fixation Engaged")
            elif "Fixation Released" in line:
                fixation_label.config(text="Fixation: INACTIVE", fg="red")
                add_console_log("Fixation Released")
            elif "Escape Event" in line:
                fixation_label.config(text="Fixation: ESCAPE", fg="yellow")
                add_console_log("Escape Event")
            elif "Time-Up Release" in line:
                fixation_label.config(text="Fixation: TIME UP", fg="purple")
                add_console_log("Time-Up Release")

            if "Struggle YES" in line:
                struggle_label.config(text="Struggle: YES", fg="red")
                add_console_log("Struggle Detected - Released")
            elif "Struggle NO" in line:
                struggle_label.config(text="Struggle: NO", fg="gray")

            if "Reward Given" in line:
                add_console_log("Reward Given")

        except:
            pass  # don't crash if serial reading fails once

# starts a session and timer
def start_session():
    global session_start_time, timer_running
    session_start_time = time.time()
    timer_running = True
    ser.write(b'b')  # tell Arduino session started
    add_console_log(f"Session Started for {rat_name_entry.get()}")

# stops session (but does not clear totals)
def stop_session():
    global timer_running
    timer_running = False
    ser.write(b'c')  # tell Arduino session stopped
    add_console_log("Session Stopped")

# runs the serial listener in the background
def start_serial_thread():
    threading.Thread(target=update_serial, daemon=True).start()


# === GUI BUILDING STARTS HERE ===
root = tk.Tk()
root.title("Head-Fixation Control Panel")
root.configure(bg="#f2f2f2")

# --- Top bar (rat name + timer) ---
top_frame = tk.Frame(root, bg="#d9e6f2", pady=10)
top_frame.grid(row=0, column=0, columnspan=4, sticky="ew")

tk.Label(top_frame, text="Rat Name:", bg="#d9e6f2", font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=5)
rat_name_entry = tk.Entry(top_frame, width=15)
rat_name_entry.insert(0, "Rat 1")
rat_name_entry.pack(side=tk.LEFT, padx=5)

session_timer_label = tk.Label(top_frame, text="Session Time: 00:00:00", bg="#d9e6f2", font=("Arial", 12))
session_timer_label.pack(side=tk.RIGHT, padx=20)

# --- Fixation & Struggle labels ---
fixation_label = tk.Label(root, text="Fixation: INACTIVE", fg="red", font=("Arial", 14), bg="#f2f2f2")
fixation_label.grid(row=1, column=0, columnspan=2, pady=5)

struggle_label = tk.Label(root, text="Struggle: NO", fg="gray", font=("Arial", 12), bg="#f2f2f2")
struggle_label.grid(row=2, column=0, columnspan=2, pady=2)

# --- Parameter controls ---
param_frame = tk.LabelFrame(root, text="Parameters", bg="#f2f2f2", font=("Arial", 10, "bold"))
param_frame.grid(row=3, column=0, columnspan=2, padx=5, pady=5)

# helper for building rows of entry + set button
def make_param_row(parent, text, default, command):
    frame = tk.Frame(parent, bg="#f2f2f2")
    frame.pack(pady=3)
    tk.Label(frame, text=text, bg="#f2f2f2").pack(side=tk.LEFT)
    entry = tk.Entry(frame, width=6)
    entry.insert(0, str(default))
    entry.pack(side=tk.LEFT, padx=3)
    tk.Button(frame, text="Set", bg="#4CAF50", fg="white", command=command).pack(side=tk.LEFT)
    return entry

threshold_entry = make_param_row(param_frame, "Struggle (g):", struggle_threshold, send_threshold)
fix_duration_entry = make_param_row(param_frame, "Fix Duration (s):", fix_duration, send_fix_duration)
fix_delay_entry = make_param_row(param_frame, "Fix Delay (s):", fix_delay, send_fix_delay)
escape_buffer_entry = make_param_row(param_frame, "Escape Buffer (ms):", escape_buffer, send_escape_buffer)
reward_buffer_entry = make_param_row(param_frame, "Reward Buffer (ms):", reward_buffer, send_reward_buffer)

# --- Control buttons ---
flush_button = tk.Button(root, text="Flush Water", width=20, bg="#2196F3", fg="white", command=toggle_flush)
flush_button.grid(row=8, column=0, columnspan=2, pady=5)

free_reward_var = tk.BooleanVar(value=True)
free_reward_check = tk.Checkbutton(root, text="Allow Free Rewards", variable=free_reward_var,
                                   command=toggle_free_reward, bg="#f2f2f2")
free_reward_check.grid(row=9, column=0, columnspan=2, pady=5)

habituation_var = tk.BooleanVar(value=False)
habituation_check = tk.Checkbutton(root, text="Habituation Mode", variable=habituation_var,
                                   command=toggle_habituation, bg="#f2f2f2")
habituation_check.grid(row=10, column=0, columnspan=2, pady=5)

emergency_button = tk.Button(root, text="EMERGENCY RELEASE", bg="red", fg="white", width=20,
                             command=lambda: ser.write(b'j'))
emergency_button.grid(row=11, column=0, columnspan=2, pady=5)

# --- Spout movement buttons ---
spout_frame = tk.LabelFrame(root, text="Spout Movement", bg="#f2f2f2", font=("Arial", 10, "bold"))
spout_frame.grid(row=12, column=0, columnspan=2, padx=10, pady=10)

fwd_state=[False]; bkwd_state=[False]; up_state=[False]; down_state=[False]

forward_btn = tk.Button(spout_frame, text="Forward", width=12, bg="#e0e0e0",
                        command=lambda: toggle_button(forward_btn, fwd_state, b'F', b'S', "Forward"))
backward_btn = tk.Button(spout_frame, text="Backward", width=12, bg="#e0e0e0",
                         command=lambda: toggle_button(backward_btn, bkwd_state, b'B', b'S', "Backward"))
upward_btn = tk.Button(spout_frame, text="Upward", width=12, bg="#e0e0e0",
                       command=lambda: toggle_button(upward_btn, up_state, b'U', b'S', "Upward"))
downward_btn = tk.Button(spout_frame, text="Downward", width=12, bg="#e0e0e0",
                         command=lambda: toggle_button(downward_btn, down_state, b'D', b'S', "Downward"))

forward_btn.grid(row=0, column=0, padx=5, pady=3)
backward_btn.grid(row=0, column=1, padx=5, pady=3)
upward_btn.grid(row=1, column=0, padx=5, pady=3)
downward_btn.grid(row=1, column=1, padx=5, pady=3)

# --- Console log box ---
console_frame = tk.LabelFrame(root, text="Console Log", bg="#f2f2f2")
console_frame.grid(row=0, column=3, rowspan=6, padx=10, pady=5)
console_box = tk.Text(console_frame, width=40, height=10, bg="#ffffff")
console_box.pack()

# --- Buttons for session control ---
button_frame = tk.Frame(root, bg="#f2f2f2")
button_frame.grid(row=6, column=3, pady=5)

start_button = tk.Button(button_frame, text="Start Session", width=12, bg="#4CAF50", fg="white", command=start_session)
start_button.grid(row=0, column=0, padx=3)

stop_button = tk.Button(button_frame, text="Stop Session", width=12, bg="#F44336", fg="white", command=stop_session)
stop_button.grid(row=0, column=1, padx=3)

save_button = tk.Button(button_frame, text="Save Data", width=12, bg="#FF9800", fg="white", command=save_data_to_file)
save_button.grid(row=0, column=2, padx=3)

clear_button = tk.Button(button_frame, text="Clear Table", width=12, bg="#9C27B0", fg="white",
                         command=lambda: [reset_table(), add_console_log("Data Table Cleared")])
clear_button.grid(row=0, column=3, padx=3)

# --- Data table for trial info ---
columns = ["Time(s)", "Fix", "Escape", "TimeUp", "Struggle", "Reward"]
table_frame = tk.LabelFrame(root, text="Trial Data", bg="#f2f2f2", font=("Arial", 10, "bold"))
table_frame.grid(row=7, column=3, rowspan=4, padx=10, pady=5)

table_labels = []
for r in range(3):
    row_labels = []
    for c in range(len(columns)):
        text = columns[c] if r == 0 else "0"
        lbl = tk.Label(table_frame, text=text, width=10, relief=tk.GROOVE, bg="#ffffff")
        lbl.grid(row=r, column=c)
        row_labels.append(lbl)
    table_labels.append(row_labels)

def reset_table():
    for r in range(1, 3):
        for c in range(len(columns)):
            table_labels[r][c].config(text="0")
    for k in totals: totals[k] = 0

# --- Actuator level buttons ---
level_frame = tk.LabelFrame(root, text="Actuator Levels", bg="#f2f2f2", font=("Arial", 10, "bold"))
level_frame.grid(row=12, column=3, padx=10, pady=10)

def send_level(level):
    ser.write(f"L{level}\n".encode())
    add_console_log(f"Actuator Level {level} Selected")

for i in range(1, 6):
    tk.Button(level_frame, text=f"Level {i}", width=10, bg="#607D8B", fg="white",
              command=lambda i=i: send_level(i)).grid(row=0, column=i-1, padx=3, pady=3)

# start serial listener and timer update loop
start_serial_thread()
update_session_timer()
root.mainloop()
