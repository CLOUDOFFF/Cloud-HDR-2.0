<#
    Cloud HDR Ears Core — свои уши.

    ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ. Постоянное прослушивание в Cloud HDR раньше
    держалось на webkitSpeechRecognition: звук с микрофона уезжал в чужой
    сервис, оттуда возвращался текст. Это плохо по двум причинам сразу, и
    вторая хуже первой.

      1. Это единственное место, где приложение выходило за пределы
         компьютера. Всё остальное — разбор, решение, выполнение — своё и
         локальное, а голос уезжал наружу.

      2. В настольном приложении это просто не работает. Ярлык запускает
         CloudHDR.exe на WebView2, а WebView2 не поддерживает распознавание
         речи вовсе — движок есть только в полном Chrome. То есть режим,
         который считался рабочим, в самом приложении молчал всегда.

    Поэтому уши здесь свои, целиком: звук берётся с микрофона напрямую через
    winmm, признаки считаются нашим кодом, решение принимает наше
    сопоставление. Наружу не уходит ничего и незачем — распознавание кончается
    в этом же процессе.

    ЧЕГО ЭТИ УШИ НЕ УМЕЮТ, и это честная граница. Это не диктовка. Общую
    русскую речь с нуля не распознать без корпуса на тысячи часов, которого
    здесь нет и взять негде. Зато задача приложения другая и куда уже: принять
    ПОВЕЛИТЕЛЬНУЮ КОМАНДУ из закрытого словаря — «открой», «закрой», «сверни»
    плюс имена программ, которые на этом компьютере стоят. Словарь известен
    заранее и невелик, а для закрытого словаря сопоставление с эталоном
    работает честно и быстро.

    ОТКУДА ЭТАЛОНЫ, ЕСЛИ ГОЛОС НИКТО НЕ ЗАПИСЫВАЛ. Из голоса самой Windows.
    В системе стоит офлайновый синтезатор Irina (ru-RU) — тот же, которым
    помощник отвечает вслух. Каждая фраза словаря проговаривается им, с неё
    снимаются признаки, и это первый эталон. Поэтому уши слышат с первого
    запуска, без ритуала «а теперь наговорите сорок фраз». Синтезированный
    голос — не человеческий, и точность на нём ниже, поэтому эталоны
    дообучаются на живом голосе: каждая уверенно принятая фраза добавляется в
    набор эталонов и вытесняет синтетический. Через несколько дней обычной
    работы словарь целиком переходит на голос хозяина.

    ПОЧЕМУ МГНОВЕННО. Решение принимается на конце фразы — как только
    детектор речи услышал 280 мс тишины. Ждать нечего и не у кого: признаки
    уже посчитаны по ходу речи, сопоставление занимает единицы миллисекунд.
    Прежняя схема ждала, пока чужой сервис пришлёт «финал», и это была
    основная задержка.
#>

# ------------------------------------------------------------------ Win32 ----
#
# Захват идёт через waveIn из winmm, а не через WASAPI. WASAPI новее и умеет
# больше, но требует COM-интерфейсов, которых в PowerShell нет без своей
# обвязки; waveIn же — пять функций и структура, и он никуда не делся из
# Windows 11. Для потока «16 кГц моно» разницы в качестве нет никакой.
#
# Разбор звука написан на C#, а не на PowerShell, по той же причине, что и
# ввод в cursor-core.ps1: это арифметика в тесных циклах. Одно окно MFCC —
# это 512-точечное преобразование Фурье, окон в секунде сто, и на PowerShell
# счёт отстал бы от речи.

if (-not ('CloudHdrEars.Mic' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace CloudHdrEars {

    // ------------------------------------------------------------- звук ----

    [StructLayout(LayoutKind.Sequential)]
    public struct WAVEHDR {
        public IntPtr lpData;
        public uint dwBufferLength;
        public uint dwBytesRecorded;
        public IntPtr dwUser;
        public uint dwFlags;
        public uint dwLoops;
        public IntPtr lpNext;
        public IntPtr reserved;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    public class WAVEFORMATEX {
        public ushort wFormatTag = 1;          // PCM
        public ushort nChannels = 1;
        public uint   nSamplesPerSec = 16000;
        public uint   nAvgBytesPerSec = 32000;
        public ushort nBlockAlign = 2;
        public ushort wBitsPerSample = 16;
        public ushort cbSize = 0;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct WAVEINCAPS {
        public ushort wMid;
        public ushort wPid;
        public uint vDriverVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string szPname;
        public uint dwFormats;
        public ushort wChannels;
        public ushort wReserved1;
    }

    public static class Native {
        [DllImport("winmm.dll")] public static extern uint waveInGetNumDevs();
        [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
        public static extern int waveInGetDevCapsW(IntPtr uDeviceID, out WAVEINCAPS caps, int cbCaps);

        /// <summary>Имена всех устройств записи в том порядке, в каком их видит winmm.</summary>
        public static string[] InputNames() {
            int count = (int)waveInGetNumDevs();
            string[] names = new string[count];
            for (int i = 0; i < count; i++) {
                WAVEINCAPS caps;
                if (waveInGetDevCapsW((IntPtr)i, out caps, Marshal.SizeOf(typeof(WAVEINCAPS))) == 0)
                    names[i] = caps.szPname;
                else
                    names[i] = "(устройство " + i + ")";
            }
            return names;
        }
        [DllImport("winmm.dll")] public static extern int waveInOpen(out IntPtr phwi, int uDeviceID,
            WAVEFORMATEX pwfx, IntPtr dwCallback, IntPtr dwInstance, int fdwOpen);
        [DllImport("winmm.dll")] public static extern int waveInClose(IntPtr hwi);
        [DllImport("winmm.dll")] public static extern int waveInPrepareHeader(IntPtr hwi, IntPtr pwh, int cbwh);
        [DllImport("winmm.dll")] public static extern int waveInUnprepareHeader(IntPtr hwi, IntPtr pwh, int cbwh);
        [DllImport("winmm.dll")] public static extern int waveInAddBuffer(IntPtr hwi, IntPtr pwh, int cbwh);
        [DllImport("winmm.dll")] public static extern int waveInStart(IntPtr hwi);
        [DllImport("winmm.dll")] public static extern int waveInStop(IntPtr hwi);
        [DllImport("winmm.dll")] public static extern int waveInReset(IntPtr hwi);
    }

    /// <summary>Готовая фраза: куски звука между началом речи и тишиной.</summary>
    public class Utterance {
        public float[] Samples;
        public double Peak;        // громче всего за фразу
        public double Floor;       // уровень тишины в комнате на тот момент
        public double Seconds;
        public DateTime At;
    }

    /*
        Микрофон.

        Восемь буферов по 100 мс. Меньше нельзя: пока PowerShell занят
        разбором предыдущей фразы, звук продолжает идти, и очередь из восьми
        даёт почти секунду запаса — за это время буфер точно вернётся в
        оборот. Больше тоже незачем: это память, которая просто лежит.

        Опрос вместо callback сделан намеренно. waveIn умеет звать функцию
        обратного вызова, но эта функция вызывается из чужого потока звуковой
        подсистемы, и делать в ней хоть что-то сложнее копирования памяти
        нельзя. Через делегат из .NET это к тому же требует, чтобы делегат
        пережил сборщик мусора. Опрос флага WHDR_DONE раз в 10 мс стоит долей
        процента процессора и не требует ни того, ни другого.
    */
    public class Mic {
        const int RATE = 16000;
        const int BUFFERS = 8;
        const int BUF_SAMPLES = 1600;               // 100 мс
        const int BUF_BYTES = BUF_SAMPLES * 2;
        const uint WHDR_DONE = 0x00000001;

        IntPtr handle = IntPtr.Zero;
        IntPtr[] headers = new IntPtr[BUFFERS];
        IntPtr[] datas = new IntPtr[BUFFERS];
        Thread pump;
        volatile bool running;

        public string Error = "";

        // --- детектор речи ---
        const int FRAME = 160;                      // 10 мс
        const int HISTORY = 1000;                   // 10 с громкости

        /*
            Громкость последних десяти секунд лежит в кольце, а не в List.

            Раньше здесь был List на тысячу чисел, из которого на каждом кадре
            удалялся нулевой элемент, — а это сдвиг всей тысячи, сто раз в
            секунду. Кольцо пишет одно число на место самого старого и не
            двигает ничего.
        */
        readonly double[] history = new double[HISTORY];
        readonly double[] sorted = new double[HISTORY];
        int historyAt = 0, historyCount = 0;
        int sinceFloor = 25;

        readonly List<float> current = new List<float>();
        readonly Queue<Utterance> ready = new Queue<Utterance>();
        readonly object gate = new object();

        double noiseFloor = 0.01;
        double peak = 0;
        int voiced = 0, quiet = 0;
        bool inSpeech = false;
        float[] tail = new float[0];

        /// <summary>Во сколько раз голос должен быть громче тишины комнаты.</summary>
        public double NearRatio = 2.4;
        /// <summary>Абсолютный минимум громкости — на случай идеально тихой комнаты.</summary>
        public double AbsFloor = 0.020;
        /// <summary>Сколько тишины считать концом фразы, в кадрах по 10 мс.</summary>
        public int SilenceFrames = 28;
        public int MinFrames = 22;                  // короче 220 мс — это не команда, а стук
        public int MaxFrames = 700;                 // 7 с потолок

        public double Level { get { return peak; } }
        public double Floor { get { return noiseFloor; } }
        public bool Speaking { get { return inSpeech; } }
        public bool Running { get { return running; } }

        // Громче всего за всё время работы и сколько кадров вообще пришло.
        // Нужны для одной проверки, которую иначе не сделать: отличить тихую
        // комнату от неработающего микрофона. И то, и другое выглядит как
        // «уровень около нуля», но в первом случае кадры идут, а во втором
        // их нет вовсе — и лечится это совершенно по-разному.
        public double RawPeak = 0;
        public long Frames = 0;

        public bool Start(int device) {
            if (running) return true;
            Error = "";
            if (Native.waveInGetNumDevs() == 0) { Error = "микрофон не найден"; return false; }

            WAVEFORMATEX format = new WAVEFORMATEX();
            int code = Native.waveInOpen(out handle, device, format, IntPtr.Zero, IntPtr.Zero, 0);
            if (code != 0) { Error = "микрофон занят другой программой (код " + code + ")"; return false; }

            for (int i = 0; i < BUFFERS; i++) {
                datas[i] = Marshal.AllocHGlobal(BUF_BYTES);
                WAVEHDR header = new WAVEHDR();
                header.lpData = datas[i];
                header.dwBufferLength = BUF_BYTES;
                headers[i] = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(WAVEHDR)));
                Marshal.StructureToPtr(header, headers[i], false);
                Native.waveInPrepareHeader(handle, headers[i], Marshal.SizeOf(typeof(WAVEHDR)));
                Native.waveInAddBuffer(handle, headers[i], Marshal.SizeOf(typeof(WAVEHDR)));
            }

            running = true;
            Native.waveInStart(handle);
            pump = new Thread(Pump);
            pump.IsBackground = true;
            pump.Priority = ThreadPriority.AboveNormal;
            pump.Start();
            return true;
        }

        public void Stop() {
            if (!running) return;
            running = false;
            try { if (pump != null) pump.Join(500); } catch { }
            try {
                Native.waveInStop(handle);
                Native.waveInReset(handle);
                for (int i = 0; i < BUFFERS; i++) {
                    if (headers[i] != IntPtr.Zero) {
                        Native.waveInUnprepareHeader(handle, headers[i], Marshal.SizeOf(typeof(WAVEHDR)));
                        Marshal.FreeHGlobal(headers[i]); headers[i] = IntPtr.Zero;
                    }
                    if (datas[i] != IntPtr.Zero) { Marshal.FreeHGlobal(datas[i]); datas[i] = IntPtr.Zero; }
                }
                Native.waveInClose(handle);
            } catch { }
            handle = IntPtr.Zero;
            lock (gate) { ready.Clear(); current.Clear(); }
            inSpeech = false;
        }

        /*
            Переоткрыть микрофон.

            Нужно, когда устройство появилось или пропало на ходу: гарнитуру
            воткнули уже после запуска, звуковую подсистему перезапустили,
            микрофон отобрала и вернула другая программа. waveIn о таком не
            сообщает никак — буферы просто перестают возвращаться, и уши
            остаются «работающими», но глухими до перезапуска приложения.

            Счётчики шума сбрасываются: устройство может оказаться другим, и
            пол тишины, снятый с прежнего, к нему отношения не имеет.
        */
        public bool Restart(int device) {
            Stop();
            historyAt = 0; historyCount = 0; sinceFloor = 25;
            noiseFloor = 0.01; peak = 0; voiced = 0; quiet = 0;
            Frames = 0; RawPeak = 0;
            tail = new float[0];
            return Start(device);
        }

        void Pump() {
            int size = Marshal.SizeOf(typeof(WAVEHDR));
            short[] pcm = new short[BUF_SAMPLES];
            while (running) {
                bool idle = true;
                for (int i = 0; i < BUFFERS && running; i++) {
                    WAVEHDR header = (WAVEHDR)Marshal.PtrToStructure(headers[i], typeof(WAVEHDR));
                    if ((header.dwFlags & WHDR_DONE) == 0) continue;
                    idle = false;

                    int count = (int)header.dwBytesRecorded / 2;
                    if (count > 0) {
                        Marshal.Copy(header.lpData, pcm, 0, count);
                        float[] chunk = new float[count];
                        for (int s = 0; s < count; s++) chunk[s] = pcm[s] / 32768f;
                        try { Feed(chunk); } catch { }
                    }

                    Native.waveInUnprepareHeader(handle, headers[i], size);
                    header.dwFlags = 0;
                    header.dwBytesRecorded = 0;
                    Marshal.StructureToPtr(header, headers[i], false);
                    Native.waveInPrepareHeader(handle, headers[i], size);
                    Native.waveInAddBuffer(handle, headers[i], size);
                }
                if (idle) Thread.Sleep(8);
            }
        }

        /*
            Нарезка потока на фразы.

            Пол тишины — двадцатый процентиль громкости за последние десять
            секунд, а не среднее. Среднее здесь испорчено самой речью: одна
            громкая фраза поднимает «тишину» вместе с собой, и следующая
            команда не проходит порог. Процентиль на это не ведётся — громкие
            куски остаются в верхних восьмидесяти процентах и на оценку не
            влияют.

            Порог взят тот же, что показал себя в прежнем режиме: голос
            человека у компьютера громче фонового разговора в разы, и это
            единственный признак, по которому «сказали мне» отделяется от
            «говорят рядом», — слова-то у них одинаковые.
        */
        void Feed(float[] chunk) {
            int frames = chunk.Length / FRAME;
            for (int f = 0; f < frames; f++) {
                int at = f * FRAME;
                double sum = 0;
                for (int s = 0; s < FRAME; s++) { double v = chunk[at + s]; sum += v * v; }
                double rms = Math.Sqrt(sum / FRAME);
                Frames++;
                if (rms > RawPeak) RawPeak = rms;

                history[historyAt] = rms;
                historyAt = (historyAt + 1) % HISTORY;
                if (historyCount < HISTORY) historyCount++;

                /*
                    Пол тишины пересчитывается раз в четверть секунды, а не на
                    каждом кадре.

                    Раньше на каждый кадр — сто раз в секунду — копировалась и
                    сортировалась тысяча чисел. Это шло в потоке звука, том
                    самом, который обязан успевать за микрофоном, и мусор от
                    тысячи копий в секунду доставался сборщику. Комнатный шум
                    так быстро не меняется: за 250 мс он тот же самый, а работы
                    здесь становится в двадцать пять раз меньше.
                */
                if (historyCount >= 40 && ++sinceFloor >= 25) {
                    sinceFloor = 0;
                    Array.Copy(history, sorted, historyCount);
                    Array.Sort(sorted, 0, historyCount);
                    noiseFloor = Math.Max(0.0015, sorted[(int)(historyCount * 0.2)]);
                }

                double need = Math.Max(AbsFloor * 0.35, noiseFloor * 1.9);
                bool loud = rms > need;

                if (!inSpeech) {
                    // Хвост держим всегда: речь опознаётся со второго кадра, и
                    // без запаса у каждой фразы срезалось бы начало — а начало
                    // у команды несёт глагол, то есть самое важное.
                    Push(ref tail, chunk, at, 12);
                    if (loud) { voiced++; } else { voiced = 0; }
                    if (voiced >= 2) {
                        inSpeech = true; quiet = 0; peak = 0;
                        current.Clear();
                        current.AddRange(tail);
                    }
                } else {
                    for (int s = 0; s < FRAME; s++) current.Add(chunk[at + s]);
                    if (rms > peak) peak = rms;
                    if (loud) quiet = 0; else quiet++;

                    bool tooLong = current.Count / FRAME >= MaxFrames;
                    if (quiet >= SilenceFrames || tooLong) {
                        int total = current.Count / FRAME;
                        inSpeech = false; voiced = 0;
                        if (total >= MinFrames) {
                            Utterance said = new Utterance();
                            said.Samples = current.ToArray();
                            said.Peak = peak;
                            said.Floor = noiseFloor;
                            said.Seconds = said.Samples.Length / (double)RATE;
                            said.At = DateTime.Now;
                            lock (gate) {
                                ready.Enqueue(said);
                                while (ready.Count > 6) ready.Dequeue();   // старое не догонять
                            }
                        }
                        current.Clear();
                    }
                }
            }
        }

        static void Push(ref float[] buffer, float[] chunk, int at, int keepFrames) {
            int keep = keepFrames * FRAME;
            float[] next = new float[Math.Min(keep, buffer.Length + FRAME)];
            int take = next.Length - FRAME;
            if (take > 0) Array.Copy(buffer, buffer.Length - take, next, 0, take);
            Array.Copy(chunk, at, next, Math.Max(0, next.Length - FRAME), Math.Min(FRAME, next.Length));
            buffer = next;
        }

        /// <summary>Забрать готовую фразу; null — пока ничего не сказано.</summary>
        public Utterance Take() {
            lock (gate) { return ready.Count > 0 ? ready.Dequeue() : null; }
        }

        /// <summary>Забыть накопленное — например, пока помощник говорит сам.</summary>
        public void Flush() {
            lock (gate) { ready.Clear(); }
            current.Clear(); inSpeech = false; voiced = 0;
        }
    }

    // ---------------------------------------------------------- признаки ----

    /*
        MFCC — мел-частотные кепстральные коэффициенты.

        Почему именно они, а не спектр как есть. Спектр слишком подробен: он
        описывает в том числе высоту голоса и тембр, а это ровно то, что
        отличает одного говорящего от другого и должно быть выброшено. Мел-шкала
        сжимает частоты так, как их различает ухо (внизу подробно, вверху
        грубо), логарифм убирает зависимость от громкости, а обратное косинусное
        преобразование разносит «что сказано» и «кто сказал» по разным
        коэффициентам — первые тринадцать несут первое.

        Вычитание среднего по фразе (CMN) — не украшение, а необходимость.
        Микрофон, комната и расстояние до рта дают постоянный сдвиг всему
        кепстру; без вычитания эталон, снятый с синтезатора, не совпал бы с
        живым голосом никогда.
    */
    public static class Dsp {
        public const int RATE = 16000;
        public const int WINDOW = 400;      // 25 мс
        public const int HOP = 160;         // 10 мс
        public const int NFFT = 512;
        public const int BANDS = 26;
        public const int COEFS = 13;

        static double[] hamming;
        static double[][] filters;
        static double[][] dct;

        static Dsp() {
            hamming = new double[WINDOW];
            for (int i = 0; i < WINDOW; i++)
                hamming[i] = 0.54 - 0.46 * Math.Cos(2 * Math.PI * i / (WINDOW - 1));

            // Мел-фильтры от 300 Гц до 8000: ниже 300 в речи только гул сети и
            // стук по столу, выше 8000 на 16 кГц нет ничего.
            double lo = Mel(300), hi = Mel(7800);
            double[] points = new double[BANDS + 2];
            for (int i = 0; i < points.Length; i++) {
                double mel = lo + (hi - lo) * i / (BANDS + 1);
                points[i] = Math.Floor((NFFT + 1) * Hz(mel) / RATE);
            }
            filters = new double[BANDS][];
            for (int b = 0; b < BANDS; b++) {
                filters[b] = new double[NFFT / 2 + 1];
                int left = (int)points[b], mid = (int)points[b + 1], right = (int)points[b + 2];
                for (int k = left; k < mid && k < filters[b].Length; k++)
                    if (mid > left) filters[b][k] = (k - left) / (double)(mid - left);
                for (int k = mid; k < right && k < filters[b].Length; k++)
                    if (right > mid) filters[b][k] = (right - k) / (double)(right - mid);
            }

            dct = new double[COEFS][];
            for (int c = 0; c < COEFS; c++) {
                dct[c] = new double[BANDS];
                for (int b = 0; b < BANDS; b++)
                    dct[c][b] = Math.Cos(Math.PI * c * (b + 0.5) / BANDS);
            }
        }

        static double Mel(double hz) { return 2595 * Math.Log10(1 + hz / 700); }
        static double Hz(double mel) { return 700 * (Math.Pow(10, mel / 2595) - 1); }

        /// <summary>Быстрое преобразование Фурье на месте, размер — степень двойки.</summary>
        static void Fft(double[] re, double[] im) {
            int n = re.Length;
            for (int i = 1, j = 0; i < n; i++) {
                int bit = n >> 1;
                for (; (j & bit) != 0; bit >>= 1) j ^= bit;
                j ^= bit;
                if (i < j) {
                    double t = re[i]; re[i] = re[j]; re[j] = t;
                    t = im[i]; im[i] = im[j]; im[j] = t;
                }
            }
            for (int len = 2; len <= n; len <<= 1) {
                double angle = -2 * Math.PI / len;
                double wr = Math.Cos(angle), wi = Math.Sin(angle);
                for (int i = 0; i < n; i += len) {
                    double cr = 1, ci = 0;
                    for (int k = 0; k < len / 2; k++) {
                        int a = i + k, b = i + k + len / 2;
                        double xr = re[b] * cr - im[b] * ci;
                        double xi = re[b] * ci + im[b] * cr;
                        re[b] = re[a] - xr; im[b] = im[a] - xi;
                        re[a] += xr;        im[a] += xi;
                        double nr = cr * wr - ci * wi;
                        ci = cr * wi + ci * wr; cr = nr;
                    }
                }
            }
        }

        /// <summary>Признаки фразы: массив кадров по 13 коэффициентов.</summary>
        public static double[][] Mfcc(float[] samples) {
            if (samples == null || samples.Length < WINDOW) return new double[0][];

            // Предыскажение поднимает верхние частоты, заваленные у любого
            // микрофона; без него согласные читаются хуже гласных, а команды
            // различаются как раз согласными.
            double[] pre = new double[samples.Length];
            pre[0] = samples[0];
            for (int i = 1; i < samples.Length; i++) pre[i] = samples[i] - 0.97 * samples[i - 1];

            int count = 1 + (pre.Length - WINDOW) / HOP;
            List<double[]> frames = new List<double[]>(count);
            double[] re = new double[NFFT], im = new double[NFFT];
            double[] energy = new double[BANDS];

            for (int f = 0; f < count; f++) {
                int at = f * HOP;
                Array.Clear(re, 0, NFFT); Array.Clear(im, 0, NFFT);
                for (int i = 0; i < WINDOW; i++) re[i] = pre[at + i] * hamming[i];
                Fft(re, im);

                for (int b = 0; b < BANDS; b++) {
                    double sum = 0;
                    double[] filter = filters[b];
                    for (int k = 0; k < filter.Length; k++) {
                        if (filter[k] == 0) continue;
                        sum += filter[k] * (re[k] * re[k] + im[k] * im[k]);
                    }
                    energy[b] = Math.Log(sum + 1e-10);
                }

                double[] coefs = new double[COEFS];
                for (int c = 0; c < COEFS; c++) {
                    double sum = 0;
                    for (int b = 0; b < BANDS; b++) sum += energy[b] * dct[c][b];
                    coefs[c] = sum;
                }
                frames.Add(coefs);
            }

            double[][] result = frames.ToArray();
            Normalize(result);
            return result;
        }

        /// <summary>Вычесть среднее по фразе и привести разброс к единице.</summary>
        static void Normalize(double[][] frames) {
            if (frames.Length == 0) return;
            for (int c = 0; c < COEFS; c++) {
                double mean = 0;
                for (int f = 0; f < frames.Length; f++) mean += frames[f][c];
                mean /= frames.Length;
                double variance = 0;
                for (int f = 0; f < frames.Length; f++) {
                    double d = frames[f][c] - mean; variance += d * d;
                }
                double sd = Math.Sqrt(variance / frames.Length);
                if (sd < 1e-6) sd = 1;
                for (int f = 0; f < frames.Length; f++) frames[f][c] = (frames[f][c] - mean) / sd;
            }
        }

        /*
            DTW — сопоставление с растяжением по времени.

            Одну и ту же команду говорят то быстрее, то медленнее, и растягивают
            при этом не всю фразу поровну, а как придётся: гласные тянутся,
            согласные нет. Поэтому кадр к кадру сравнивать нельзя — нужно
            выравнивание, которое само найдёт соответствие.

            Полоса Сакоэ—Чибы ограничивает выравнивание разумным коридором.
            Без неё «открой» может выравняться на «открой блокнот» за счёт
            бесконечного растягивания одного звука, и цена такого совпадения
            выйдет обманчиво низкой.
        */
        public static double Dtw(double[][] a, double[][] b, double bandRatio) {
            int n = a.Length, m = b.Length;
            if (n == 0 || m == 0) return double.MaxValue;

            int band = (int)Math.Max(Math.Abs(n - m) + 1, Math.Max(n, m) * bandRatio);
            double[] prev = new double[m + 1];
            double[] curr = new double[m + 1];
            for (int j = 0; j <= m; j++) prev[j] = double.MaxValue;
            prev[0] = 0;

            for (int i = 1; i <= n; i++) {
                int from = Math.Max(1, i * m / n - band);
                int to = Math.Min(m, i * m / n + band);
                for (int j = 0; j <= m; j++) curr[j] = double.MaxValue;
                for (int j = from; j <= to; j++) {
                    double cost = 0;
                    double[] fa = a[i - 1], fb = b[j - 1];
                    for (int c = 0; c < COEFS; c++) { double d = fa[c] - fb[c]; cost += d * d; }
                    cost = Math.Sqrt(cost);
                    double best = prev[j];
                    if (prev[j - 1] < best) best = prev[j - 1];
                    if (curr[j - 1] < best) best = curr[j - 1];
                    if (best == double.MaxValue) { curr[j] = double.MaxValue; continue; }
                    curr[j] = cost + best;
                }
                double[] swap = prev; prev = curr; curr = swap;
            }
            if (prev[m] == double.MaxValue) return double.MaxValue;
            return prev[m] / (n + m);      // на кадр пути — иначе длинные фразы всегда «дальше»
        }
    }

    // ------------------------------------------------------- сопоставление --

    public class Match {
        public string Phrase = "";
        public string Intent = "";
        public string Source = "";
        public double Cost = double.MaxValue;
        public double Gap;             // отрыв от второго места
        public string Rival = "";
        public int Compared;
        public double Millis;
    }

    /*
        Перебор эталонов.

        Вынесен в C# целиком, а не оставлен в PowerShell, по замеру: словарь
        под сотню фраз, каждое сравнение — матрица примерно 200 на 80. В
        PowerShell цикл по эталонам с вызовом Dtw на каждом шаге давал около
        полутора секунд, и «мгновенно» превращалось в «примерно тогда же, когда
        и раньше». Здесь тот же перебор укладывается в единицы миллисекунд,
        и это разница между «услышал и сделал» и «услышал, подумал, сделал».

        Отсев по длине — до всякой арифметики. Фраза вдвое короче эталона не
        может оказаться им ни при каком выравнивании, а стоит эта проверка
        одного сравнения целых чисел. На словаре из сотни фраз она отбрасывает
        больше половины.
    */
    public class Matcher {
        readonly List<double[][]> frames = new List<double[][]>();
        readonly List<string> phrases = new List<string>();
        readonly List<string> intents = new List<string>();
        readonly List<string> sources = new List<string>();
        readonly object gate = new object();

        public double Band = 0.25;

        public int Count { get { lock (gate) { return frames.Count; } } }

        public void Add(string phrase, string intent, string source, double[][] features) {
            if (features == null || features.Length == 0) return;
            lock (gate) {
                phrases.Add(phrase); intents.Add(intent); sources.Add(source); frames.Add(features);
            }
        }

        public void Clear() {
            lock (gate) { phrases.Clear(); intents.Clear(); sources.Clear(); frames.Clear(); }
        }

        public Match Best(double[][] said) {
            Match result = new Match();
            if (said == null || said.Length == 0) return result;
            DateTime started = DateTime.Now;
            double second = double.MaxValue;
            string secondPhrase = "";
            int compared = 0;

            lock (gate) {
                for (int i = 0; i < frames.Count; i++) {
                    double[][] pattern = frames[i];
                    double ratio = said.Length / (double)pattern.Length;
                    if (ratio < 0.5 || ratio > 2.0) continue;
                    compared++;

                    // Второе место считается среди ДРУГИХ фраз, а не среди
                    // эталонов. У одной фразы эталонов несколько — синтез плюс
                    // живые записи, — и без этой оговорки отрыв мерился бы от
                    // неё же самой и всегда выходил бы около нуля. То есть чем
                    // лучше помощник знает фразу, тем менее уверенным он бы
                    // себя считал: ровно наоборот к смыслу.
                    double cost = Dsp.Dtw(said, pattern, Band);
                    if (cost < result.Cost) {
                        if (result.Phrase.Length > 0 && result.Phrase != phrases[i]) {
                            second = result.Cost; secondPhrase = result.Phrase;
                        }
                        result.Cost = cost;
                        result.Phrase = phrases[i];
                        result.Intent = intents[i];
                        result.Source = sources[i];
                    } else if (cost < second && phrases[i] != result.Phrase) {
                        second = cost; secondPhrase = phrases[i];
                    }
                }
            }

            result.Compared = compared;
            result.Rival = secondPhrase;
            result.Gap = (second == double.MaxValue) ? 99 : (second - result.Cost);
            result.Millis = (DateTime.Now - started).TotalMilliseconds;
            return result;
        }
    }
}
'@ -ReferencedAssemblies 'System.Core' -ErrorAction Stop
}

# ------------------------------------------------------- голос как эталон ----

<#
    Проговорить фразу локальным синтезатором и снять с неё признаки.

    Синтез идёт в файл, а не в динамики: озвучивать сорок фраз при первом
    запуске — это сорок секунд шума из колонок, которых никто не просил.
    Файл потом читается как обычный WAV.

    Голос берётся русский (Irina), если он есть. Английский эталон для русской
    команды бесполезен — там другая фонетика, и совпадений не будет.
#>
function New-VoiceSample([string]$phrase, [string]$path) {
    Add-Type -AssemblyName System.Speech -ErrorAction Stop
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $russian = $synth.GetInstalledVoices() |
                   Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'ru*' } |
                   Select-Object -First 1
        if ($russian) { $synth.SelectVoice($russian.VoiceInfo.Name) }

        # Формат задаётся явно под наш разбор: 16 кГц, моно, 16 бит. Синтезатор
        # по умолчанию отдаёт 22 кГц, и пересчёт частоты пришлось бы писать
        # самим — проще попросить нужное сразу.
        $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
            16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
            [System.Speech.AudioFormat.AudioChannel]::Mono)
        $synth.SetOutputToWaveFile($path, $format)
        $synth.Rate = 0
        $synth.Speak($phrase)
        $synth.SetOutputToNull()
    } finally { $synth.Dispose() }
    return (Test-Path $path)
}

<# Прочитать WAV 16 кГц моно 16 бит в массив float. #>
function Read-WaveSamples([string]$path) {
    $bytes = [IO.File]::ReadAllBytes($path)
    if ($bytes.Length -lt 44) { return @() }

    # Ищем чанк data честным обходом, а не смещением 44. Синтезатор вставляет
    # перед ним чанк fact, и жёсткое смещение дало бы сдвиг на четыре байта —
    # звук при этом не ломается, но признаки уезжают.
    $at = 12
    while ($at + 8 -lt $bytes.Length) {
        $id = [Text.Encoding]::ASCII.GetString($bytes, $at, 4)
        $size = [BitConverter]::ToInt32($bytes, $at + 4)
        if ($id -eq 'data') {
            $count = [Math]::Min($size, $bytes.Length - $at - 8) / 2
            $samples = New-Object 'float[]' $count
            for ($i = 0; $i -lt $count; $i++) {
                $samples[$i] = [BitConverter]::ToInt16($bytes, $at + 8 + $i * 2) / 32768.0
            }
            return $samples
        }
        $at += 8 + $size + ($size % 2)
    }
    return @()
}

<# Признаки фразы, произнесённой синтезатором. #>
function Get-SynthFeatures([string]$phrase) {
    $temp = Join-Path $env:TEMP ("cloudhdr-say-{0}.wav" -f ([guid]::NewGuid().ToString('N')))
    try {
        if (-not (New-VoiceSample $phrase $temp)) { return $null }
        $samples = Read-WaveSamples $temp
        if ($samples.Count -lt 400) { return $null }
        return [CloudHdrEars.Dsp]::Mfcc($samples)
    } finally {
        Remove-Item $temp -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------------------- эталоны -------

<#
    Хранилище эталонов.

    Признаки лежат в JSON рядом с логами. Формат намеренно простой — массив
    чисел на кадр, — потому что этот файл переживает обновления приложения и
    его должно быть можно прочитать глазами, когда что-то пойдёт не так.

    Коэффициенты округляются до сотых. Точность DTW от этого не страдает
    (разброс кепстра — единицы), а файл выходит втрое меньше и читаемым.
#>
function New-Template([string]$phrase, [string]$intent, $features, [string]$source) {
    $frames = @()
    foreach ($frame in $features) {
        $frames += , @($frame | ForEach-Object { [math]::Round($_, 2) })
    }
    return [ordered]@{
        phrase = $phrase
        intent = $intent
        source = $source          # synth — из синтезатора, live — живой голос
        at     = (Get-Date).ToString('s')
        frames = $frames
    }
}

function ConvertTo-Features($template) {
    $frames = @($template.frames)
    $result = New-Object 'double[][]' $frames.Count
    for ($i = 0; $i -lt $frames.Count; $i++) {
        $row = @($frames[$i])
        $line = New-Object 'double[]' ([CloudHdrEars.Dsp]::COEFS)
        for ($c = 0; $c -lt $line.Length -and $c -lt $row.Count; $c++) { $line[$c] = [double]$row[$c] }
        $result[$i] = $line
    }
    return $result
}

<#
    Узнать фразу.

    Возвращается лучший эталон и его цена. Порог здесь не зашит: он зависит от
    того, синтетический эталон или живой, и решать это должен вызывающий —
    в ears.ps1, где известен профиль чувствительности.

    Второе место возвращается не для красоты. Уверенность в закрытом словаре
    определяется не абсолютной ценой (она пляшет от микрофона к микрофону), а
    ОТРЫВОМ первого от второго: если «открой хром» и «открой хрому» стоят
    одинаково, значит, услышано что-то третье.
#>
function Find-Phrase($features, $templates, [double]$band = 0.25) {
    if (-not $features -or $features.Count -eq 0) { return $null }
    $best = $null; $second = $null

    foreach ($template in $templates) {
        $cost = [CloudHdrEars.Dsp]::Dtw($features, $template.features, $band)
        if ($cost -eq [double]::MaxValue) { continue }
        if (-not $best -or $cost -lt $best.cost) {
            $second = $best
            $best = @{ cost = $cost; phrase = $template.phrase; intent = $template.intent
                       source = $template.source; template = $template }
        } elseif (-not $second -or $cost -lt $second.cost) {
            $second = @{ cost = $cost; phrase = $template.phrase }
        }
    }
    if (-not $best) { return $null }

    $gap = if ($second) { $second.cost - $best.cost } else { $best.cost }
    $best.gap = $gap
    $best.rival = if ($second) { $second.phrase } else { '' }
    return $best
}
