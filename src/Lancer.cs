using System; using System.IO; using System.Diagnostics; using System.Windows.Forms;
static class Launcher { [STAThread] static void Main() {
try { var s=new ProcessStartInfo(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
@"Extractor\DOA5LR-PS4-Extractor\DOA5LR-PS4-Extractor.exe"));
s.UseShellExecute=false; s.CreateNoWindow=true; Process.Start(s);
} catch(Exception e) { MessageBox.Show(e.Message,"DOA5LR PS4 Extractor"); } } }
