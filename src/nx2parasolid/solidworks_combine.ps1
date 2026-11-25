# SolidWorks backend of nx2parasolid (called by nx2parasolid.solidworks; not normally run by hand).
#   -Mode measure : load each Parasolid file, report every solid/sheet body (volume, area, centre of
#                   mass, box, faces, Check faults) as JSON lines. Opens and closes its own docs only.
#   -Mode build   : new part; copy the bodies listed in -Plan (chosen by volume/area/centre of mass,
#                   because SolidWorks does not keep body order stable) into it unchanged, run Check,
#                   save <OutBase>.SLDPRT and export ONE Parasolid <OutBase>.x_t; then re-import
#                   that .x_t and report its bodies, so the caller can compare before/after.
# Starts SolidWorks if it is not running. Never touches documents the user already has open.
param([ValidateSet("measure", "build")][string]$Mode, [string]$FileList, [string]$Plan, [string]$OutBase, [string]$Out,
      [string]$Redist = "C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist")
$ErrorActionPreference = "Stop"
$redist = $Redist
Add-Type -Path "$redist\SolidWorks.Interop.sldworks.dll"
Add-Type -Path "$redist\SolidWorks.Interop.swconst.dll"
$src = @"
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;

public static class SwPrt {
    static string N(double d) { return d.ToString("R", CultureInfo.InvariantCulture); }
    static string Esc(string s) { return s.Replace("\\", "\\\\").Replace("\"", "\\\""); }

    public static SldWorks Sw() {
        try { return (SldWorks)Marshal.GetActiveObject("SldWorks.Application"); }
        catch (COMException) {
            SldWorks sw = (SldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));
            sw.Visible = true;
            return sw;
        }
    }

    static List<Body2> Bodies(PartDoc p) {
        var all = new List<Body2>();
        foreach (int t in new int[] { (int)swBodyType_e.swSolidBody, (int)swBodyType_e.swSheetBody }) {
            object[] bs = (object[])p.GetBodies2(t, false);
            if (bs != null) foreach (object o in bs) all.Add((Body2)o);
        }
        return all;
    }

    static string BodyJson(Body2 b) {
        double[] m = (double[])b.GetMassProperties(1.0);
        double[] bb = (double[])b.GetBodyBox();
        IFaultEntity fe = b.Check3;
        var sb = new StringBuilder();
        sb.Append("\"solid\":" + (b.GetType() == (int)swBodyType_e.swSolidBody ? "true" : "false"));
        sb.Append(",\"faces\":" + b.GetFaceCount() + ",\"check_faults\":" + (fe == null ? 0 : fe.Count));
        if (m != null) sb.Append(",\"com_mm\":[" + N(m[0]*1e3) + "," + N(m[1]*1e3) + "," + N(m[2]*1e3) + "],\"volume_mm3\":" + N(m[3]*1e9) + ",\"area_mm2\":" + N(m[4]*1e6));
        if (bb != null) sb.Append(",\"box_mm\":[" + N(bb[0]*1e3) + "," + N(bb[1]*1e3) + "," + N(bb[2]*1e3) + "," + N(bb[3]*1e3) + "," + N(bb[4]*1e3) + "," + N(bb[5]*1e3) + "]");
        return sb.ToString();
    }

    public static string Measure(string path) {
        SldWorks sw = Sw();
        int err = 0;
        ModelDoc2 d = (ModelDoc2)sw.LoadFile4(path, "r", sw.GetImportFileData(path), ref err);
        string f = "{\"file\":\"" + Esc(path) + "\"";
        if (d == null) return f + ",\"load_error\":" + err + "}";
        string t = d.GetTitle();
        var sb = new StringBuilder();
        PartDoc p = d as PartDoc;
        if (p == null) { sw.CloseDoc(t); return f + ",\"load_error\":\"not a part\"}"; }
        int k = 0;
        foreach (Body2 b in Bodies(p)) sb.Append(f + ",\"k\":" + (k++) + "," + BodyJson(b) + "}\n");
        sw.CloseDoc(t);
        return sb.Length == 0 ? f + ",\"bodies\":0}" : sb.ToString().TrimEnd('\n');
    }

    // files[i], solid[i], expect[5*i..]: volume mm3, area mm2, com x,y,z mm. Bodies of one file are taken in one load.
    public static string Build(string[] files, bool[] solid, double[] expect, string outBase) {
        SldWorks sw = Sw();
        string tpl = sw.GetUserPreferenceStringValue((int)swUserPreferenceStringValue_e.swDefaultTemplatePart);
        ModelDoc2 target = (ModelDoc2)sw.NewDocument(tpl, 0, 0, 0);
        if (target == null) return "{\"error\":\"could not create part from template " + Esc(tpl) + "\"}";
        string ttitle = target.GetTitle();
        PartDoc tpart = (PartDoc)target;
        var msgs = new List<string>();
        double worst = 0;
        var order = new List<string>();
        foreach (string f in files) if (!order.Contains(f)) order.Add(f);
        int inserted = 0;
        foreach (string f in order) {
            int err = 0;
            ModelDoc2 d = (ModelDoc2)sw.LoadFile4(f, "r", sw.GetImportFileData(f), ref err);
            if (d == null) { msgs.Add("load failed " + f); continue; }
            string t = d.GetTitle();
            List<Body2> cands = Bodies((PartDoc)d);
            var used = new HashSet<int>();
            var copies = new List<Body2>();
            for (int i = 0; i < files.Length; i++) {
                if (files[i] != f) continue;
                int best = -1; double bestScore = double.MaxValue;
                for (int c = 0; c < cands.Count; c++) {
                    if (used.Contains(c)) continue;
                    bool isSolid = cands[c].GetType() == (int)swBodyType_e.swSolidBody;
                    if (isSolid != solid[i]) continue;
                    double[] m = (double[])cands[c].GetMassProperties(1.0);
                    if (m == null) continue;
                    double score = Math.Sqrt(Math.Pow(m[0]*1e3 - expect[5*i+2], 2) + Math.Pow(m[1]*1e3 - expect[5*i+3], 2) + Math.Pow(m[2]*1e3 - expect[5*i+4], 2));
                    if (solid[i]) score += Math.Abs(m[3]*1e9 - expect[5*i]) / Math.Max(1.0, expect[5*i]) * 1e3
                                         + Math.Abs(m[4]*1e6 - expect[5*i+1]) / Math.Max(1.0, expect[5*i+1]) * 1e3;
                    if (score < bestScore) { bestScore = score; best = c; }
                }
                if (best < 0) { msgs.Add("no matching body in " + f); continue; }
                used.Add(best);
                worst = Math.Max(worst, bestScore);
                copies.Add((Body2)cands[best].Copy());
            }
            sw.CloseDoc(t);
            sw.ActivateDoc3(ttitle, false, 0, ref err);
            foreach (Body2 c in copies) {
                if (tpart.CreateFeatureFromBody3(c, false, 0) == null) msgs.Add("insert failed from " + f); else inserted++;
            }
        }
        var sb = new StringBuilder("{\"inserted\":" + inserted + ",\"requested\":" + files.Length + ",\"worst_match_score\":" + N(worst));
        sb.Append(",\"part_bodies\":[");
        bool first = true;
        foreach (Body2 b in Bodies(tpart)) { sb.Append((first ? "" : ",") + "{" + BodyJson(b) + "}"); first = false; }
        sb.Append("]");
        int e2 = 0, w2 = 0;
        if (!target.Extension.SaveAs3(outBase + ".SLDPRT", 0, 1, null, null, ref e2, ref w2)) msgs.Add("save SLDPRT failed " + e2);
        if (!target.Extension.SaveAs3(outBase + ".x_t", 0, 1 | 2, null, null, ref e2, ref w2)) msgs.Add("export x_t failed " + e2);
        sw.CloseDoc(target.GetTitle());
        // re-import the exported Parasolid and report what came back
        sb.Append(",\"reimport_bodies\":[");
        int err2 = 0;
        ModelDoc2 r = System.IO.File.Exists(outBase + ".x_t") ? (ModelDoc2)sw.LoadFile4(outBase + ".x_t", "r", sw.GetImportFileData(outBase + ".x_t"), ref err2) : null;
        if (r == null) msgs.Add("re-import of x_t failed " + err2);
        else {
            first = true;
            foreach (Body2 b in Bodies((PartDoc)r)) { sb.Append((first ? "" : ",") + "{" + BodyJson(b) + "}"); first = false; }
            sw.CloseDoc(r.GetTitle());
        }
        sb.Append("],\"messages\":[");
        for (int i = 0; i < msgs.Count; i++) sb.Append((i > 0 ? "," : "") + "\"" + Esc(msgs[i]) + "\"");
        sb.Append("]}");
        return sb.ToString();
    }
}
"@
Add-Type -TypeDefinition $src -ReferencedAssemblies "$redist\SolidWorks.Interop.sldworks.dll","$redist\SolidWorks.Interop.swconst.dll","System.Core"

if ($Mode -eq "measure") {
    Set-Content -Path $Out -Value "" -Encoding utf8
    foreach ($f in (Get-Content $FileList -Encoding utf8 | Where-Object { $_ })) { Add-Content -Path $Out -Value ([SwPrt]::Measure($f)) -Encoding utf8 }
} else {
    $p = Get-Content $Plan -Raw | ConvertFrom-Json
    $files = [string[]]@($p | ForEach-Object { $_.file })
    $solid = [bool[]]@($p | ForEach-Object { [bool]$_.solid })
    $exp = New-Object System.Collections.Generic.List[double]
    foreach ($b in $p) { $exp.AddRange([double[]]@($b.volume_mm3, $b.area_mm2, $b.com_mm[0], $b.com_mm[1], $b.com_mm[2])) }
    Set-Content -Path $Out -Value ([SwPrt]::Build($files, $solid, $exp.ToArray(), $OutBase)) -Encoding utf8
}
