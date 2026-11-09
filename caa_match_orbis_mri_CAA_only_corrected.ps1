# Read-only, local identity comparison using ONLY the CAA worksheet. Requires installed Microsoft Excel.
# All outputs containing direct identifiers stay in an approved protected folder.
param(
 [string]$Workbook = 'C:\app\pdf_extractor\data\old_retool_db\orbis_01012015_01062023_amyloidangiopat.xlsx',
 [string]$Cases = 'C:\app\pdf_extractor\data\old_retool_db\case.csv',
 [string]$MRI = 'C:\app\pdf_extractor\caa_private_linkage\mri_identity_series_PRIVATE.csv',
 [string]$OutDir = 'C:\app\pdf_extractor\caa_private_linkage'
)
$ErrorActionPreference = 'Stop'
function Norm([object]$v) {
 $s = ([string]$v).Trim().ToUpperInvariant().Normalize([Text.NormalizationForm]::FormD)
 $sb = [Text.StringBuilder]::new()
 foreach($ch in $s.ToCharArray()) {
  if([Globalization.CharUnicodeInfo]::GetUnicodeCategory($ch) -ne [Globalization.UnicodeCategory]::NonSpacingMark -and [char]::IsLetterOrDigit($ch)) { [void]$sb.Append($ch) }
 }
 return $sb.ToString()
}
function Dob([object]$v) {
 if($null -eq $v -or ([string]$v).Trim() -eq '') { return '' }
 if($v -is [double] -or $v -is [int]) {
  try { return [datetime]::FromOADate([double]$v).ToString('yyyyMMdd') } catch { return '' }
 }
 $s = ([string]$v).Trim()
 if($s -match '^\d{8}$') {
  foreach($fmt in @('yyyyMMdd','ddMMyyyy')) {
   try { return [datetime]::ParseExact($s,$fmt,[Globalization.CultureInfo]::InvariantCulture).ToString('yyyyMMdd') } catch {}
  }
 }
 if($s -match '^\d{4}-\d\d-\d\d') { return $s.Substring(0,10).Replace('-','') }
 foreach($fmt in @('dd.MM.yyyy','d.M.yyyy','dd/MM/yyyy','d/M/yyyy')) {
  try { return [datetime]::ParseExact($s,$fmt,[Globalization.CultureInfo]::InvariantCulture).ToString('yyyyMMdd') } catch {}
 }
 return ''
}
function Fall([object]$v) {
 $s = ([string]$v).Trim()
 if($s -match '^\d+\.0+$') { $s = $s.Split('.')[0] }
 return $s
}
foreach($p in @($Workbook,$Cases,$MRI)) { if(!(Test-Path -LiteralPath $p)) { throw "Input missing: $p" } }
if(!(Test-Path -LiteralPath $OutDir -PathType Container)) { throw "Protected directory missing: $OutDir" }
$caseById=@{}
foreach($r in (Import-Csv -LiteralPath $Cases -Encoding UTF8)) {
 $f=Fall $r.fallnr
 if(!$f) {continue}
 if($caseById.ContainsKey($f)) {throw 'Duplicate fallnr in case.csv'}
 $caseById[$f]=Dob $r.date_of_birth
}
$excel=$null; $book=$null
$orbisRows=[Collections.Generic.List[object]]::new()
try {
 $excel=New-Object -ComObject Excel.Application
 $excel.Visible=$false; $excel.DisplayAlerts=$false
 $book=$excel.Workbooks.Open($Workbook,0,$true)
 foreach($sheetName in @('CAA')) {
  $ws=$book.Worksheets.Item($sheetName)
  $data=$ws.UsedRange.Value2
  $header=@{}
  for($col=1; $col -le $data.GetLength(1); $col++) { $header[[string]$data[1,$col]]=$col }
  foreach($needed in @('Nachname','Vorname','Geburtsdatum','Fallnummer')) {
   if(!$header.ContainsKey($needed)) { throw "Column $needed missing in sheet $sheetName" }
  }
  for($row=2; $row -le $data.GetLength(0); $row++) {
   $fallnr=Fall $data[$row,$header['Fallnummer']]
   if(!$caseById.ContainsKey($fallnr)) {continue}
   $surname=Norm $data[$row,$header['Nachname']]
   $given=Norm $data[$row,$header['Vorname']]
   $dob=Dob $data[$row,$header['Geburtsdatum']]
   if(!$surname -or !$given -or !$dob) {continue}
   $orbisRows.Add([pscustomobject]@{fallnr=$fallnr; surname=$surname; given=$given; dob=$dob; source=$sheetName})
  }
 }
} finally {
 if($book) {$book.Close($false)}
 if($excel) {$excel.Quit()}
 if($book) {[void][Runtime.InteropServices.Marshal]::ReleaseComObject($book)}
 if($excel) {[void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)}
}
# Only identities that have complete name + DOB are eligible for exact matching.
$orbisIndex=@{}
$orbisByFall=@{}
foreach($r in $orbisRows) {
 $key="$($r.surname)|$($r.given)|$($r.dob)"
 if(!$orbisIndex.ContainsKey($key)) { $orbisIndex[$key]=[Collections.Generic.HashSet[string]]::new() }
 [void]$orbisIndex[$key].Add($r.fallnr)
 if(!$orbisByFall.ContainsKey($r.fallnr)) { $orbisByFall[$r.fallnr]=[Collections.Generic.HashSet[string]]::new() }
 [void]$orbisByFall[$r.fallnr].Add($key)
}
$mriByIdentity=@{}
foreach($r in (Import-Csv -LiteralPath $MRI -Encoding UTF8)) {
 $rawName=[string]$r.patient_name_dicom
 $parts=$rawName.Split('^')
 if($parts.Count -lt 2) { continue }
 $surname=Norm $parts[0]; $given=Norm $parts[1]; $dob=Dob $r.birth_date_yyyymmdd
 if(!$surname -or !$given -or !$dob) { continue }
 $identity="$( [string]$r.dicom_patient_id )|$surname|$given|$dob"
 if(!$mriByIdentity.ContainsKey($identity)) {
  $mriByIdentity[$identity]=[pscustomobject]@{dicom_patient_id=[string]$r.dicom_patient_id; patient_name_dicom=$rawName; dob=$dob; surname=$surname; given=$given; studies=[Collections.Generic.HashSet[string]]::new()}
 }
 if($r.study_uid) {[void]$mriByIdentity[$identity].studies.Add([string]$r.study_uid)}
}
$report=[Collections.Generic.List[object]]::new()
foreach($m in $mriByIdentity.Values) {
 $key="$($m.surname)|$($m.given)|$($m.dob)"
 $candidates=if($orbisIndex.ContainsKey($key)) {@($orbisIndex[$key])} else {@()}
 $status='NO_EXACT_NAME_DOB_MATCH'; $fallnr=''
 if($candidates.Count -gt 1) { $status='AMBIGUOUS_MULTIPLE_FALLNR' }
 elseif($candidates.Count -eq 1) {
  $fallnr=[string]$candidates[0]
  if($caseById[$fallnr] -ne $m.dob) {$status='DOB_CONFLICT_WITH_CASE'}
  elseif($orbisByFall[$fallnr].Count -gt 1) {$status='ORBIS_CONFLICTING_IDENTITY_FOR_FALLNR'}
  else {$status='EXACT_NAME_DOB_CASE_DOB_CANDIDATE'}
 }
 $report.Add([pscustomobject]@{dicom_patient_id=$m.dicom_patient_id; patient_name_dicom=$m.patient_name_dicom; dicom_dob=$m.dob; mri_study_count=$m.studies.Count; candidate_fallnr=$fallnr; candidate_count=$candidates.Count; status=$status; reviewed_by=''; review_note=''})
}
# Flag one fallnr assigned to multiple DICOM identities for human review.
$duplicates=$report | Where-Object {$_.status -eq 'EXACT_NAME_DOB_CASE_DOB_CANDIDATE'} | Group-Object candidate_fallnr | Where-Object {$_.Count -gt 1}
foreach($group in $duplicates) { foreach($row in $group.Group) {$row.status='MULTIPLE_DICOM_IDENTITIES_SAME_FALLNR_REVIEW'} }
$reportPath=Join-Path $OutDir 'mri_orbis_fallnr_exact_candidates_PRIVATE.csv'
$report | Sort-Object status,candidate_fallnr | Export-Csv -LiteralPath $reportPath -NoTypeInformation -Encoding UTF8
$matchedFall=@($report | Where-Object {$_.status -eq 'EXACT_NAME_DOB_CASE_DOB_CANDIDATE'} | Select-Object -ExpandProperty candidate_fallnr -Unique)
$allFall=@($caseById.Keys)
$missing=@($allFall | Where-Object {$_ -notin $matchedFall} | Sort-Object)
# The missing list remains provisional pending manual confirmation of candidates.
$missing | ForEach-Object {[pscustomobject]@{fallnr=$_; status='NOT_YET_VERIFIED_AS_MRI_PRESENT'}} | Export-Csv -LiteralPath (Join-Path $OutDir 'fallnr_not_yet_verified_MRI_PRIVATE.csv') -NoTypeInformation -Encoding UTF8
Write-Host "Retool fallnr: $($caseById.Count) | ORBIS rows referencing them: $($orbisRows.Count)"
Write-Host "MRI identities with parseable name + DOB: $($mriByIdentity.Count)"
$report | Group-Object status | Sort-Object Name | ForEach-Object {Write-Host "$($_.Name): $($_.Count)"}
Write-Host "Cases not yet verified as MRI present: $($missing.Count)"
Write-Host 'Private candidate report:' $reportPath
Write-Host 'No PostgreSQL writes. Exact matching is still a candidate linkage pending review.'
