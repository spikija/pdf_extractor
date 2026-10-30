"""Publication-style static figures, portrait tables and a short master PDF."""
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape
import json,math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,PageBreak,Image,KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from .cohort_analysis_statistics import categorical,continuous,numeric,write_csv

BLUE='#245879';ORANGE='#A64F25';GRAY='#707A82'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                     'axes.labelsize':10,'figure.dpi':120,'savefig.dpi':300,'pdf.fonttype':42,'ps.fonttype':42})


METHODS=[
 ('Study population and frozen definition','The analysis includes all 50 patients in the supplied full-cohort run (197 source PDFs, 1,291 source pages). The previously validated CAA-positive indicator is reused unchanged: 20 positive and 30 non-positive. Non-positive includes suspected-only, explicitly negative and indeterminate patients; it does not mean CAA excluded. Source diagnoses, formal classifications, computed Boston/Edinburgh results and pathology remain distinct. These streams overlap.'),
 ('Available cases and interpretation','Percentages use available-case denominators unless otherwise specified. Cells show n/N (%) or median [25th–75th percentile] with available N. Missing is not negative. Complete source-documentation indicators describe recorded evidence, not disease absence or lifetime ascertainment. Age uses exact DOB and earliest validated admission; elapsed days are divided by 365.2425. Year-only age is not included. Quantiles use linear interpolation; age SD uses N−1.'),
 ('Admissions and first presentation','Encounter documents are grouped by validated admission date within patient; identical admission/discharge intervals are counted once. A discharge conflict is unresolved. First presentation uses the existing primary admission reason. Broad focal reasons may be refined by a clinical event on the admission date and same document, or spontaneous ICH only when a source clinical ICH event matches that admission episode’s explicit current-ICH date and spontaneous-ICH fields. Imaging alone cannot determine presentation. Competing event categories are not arbitrarily prioritized. Broad focal categories remain broad when specificity is unavailable.'),
 ('Presentation components','Components are multiple response and percentages may total more than 100%. Structured sensory status is used directly. Validated primary reasons or same-admission clinical events can establish positive components. Motor, language, visual, headache and consciousness components are missing where no corresponding structured first-admission field exists. Narrative summaries are not retrospectively interpreted. An unknown component is not a negative.'),
 ('Examination identity and counts','Brain CT, MRI and CTA remain separate. Same family/date copied identical or verbatim-nested reports are deduplicated. Different reports on one day without a validated examination identity or time remain ambiguous, making the patient’s exact examination count missing. Separate examination-day lower bounds retain those observations. Undated examinations do not generate an exact count. No CT-angiography is counted as an extra plain CT. EEG and PET use analogous identity checks. Utilization means documented examinations in supplied records, not lifetime utilization.'),
 ('Hemorrhage and markers','Clinical ICH history is the unchanged source patient indicator. Lobar ICH is a separate source radiology/criteria marker, not a retrospectively adjudicated primary ICH diagnosis: the two streams do not always agree, and discordances are exported for review. No source or patient classification is changed to reconcile them. Spontaneous ICH requires a clinical ICH event linked to a document with existing spontaneous-ICH episode evidence. Multiple ICH means at least two distinct validated dated ICH events, using existing event dates and CT continuity groups; serial CT mentions and separate lobes are not new events. Undated evidence is excluded from event counts and retained as uncertain evidence. Positive lobar/deep markers use existing findings and source locations. Combined cSS/cSAH features do not identify either component separately, and generic SAH is not cSAH. Negative acute-bleed wording does not exclude historical hemorrhage. Marker omission is missing.'),
 ('Epilepsy, EEG and function','Chronic epilepsy, seizure history and AF indicators are copied from the validated patient-level source, preserving 9 epilepsy and 11 AF patients. Subtypes use existing event/canonical fields. AF subtype distributions are restricted to patients with AF; subtype is not applicable to non-AF patients. Slowing, epileptiform EEG activity and antiseizure drugs do not establish epilepsy. mRS uses exact previously validated scores only. Ranges are not midpoint-imputed. Discharge mRS was not exported in the source run; all discharge mRS values therefore remain missing. Last mRS retains the date of the last available exact admission/discharge score.'),
 ('Antithrombotic exposure','First-discharge exposure refers to the earliest validated hospitalization, not the first later hospitalization with a medication list. Only exported discharge-list rows belonging to that encounter and discharge date are eligible. No eligible list means missing exposure. Explicitly paused/stopped antithrombotics are excluded from active recorded use. Ever exposure means any available discharge list, not lifetime use. Drug classes retain source classifications. Therapeutic anticoagulation requires an explicitly encoded therapeutic role; prophylaxis is separate. An unspecified anticoagulant role remains missing, including DOAC/VKA rows. Dose is not used to assign indication. DOAC prescription can be known while therapeutic indication is unknown.'),
 ('Mortality documentation','An explicit patient Exitus letalis declaration in retained diagnosis candidates establishes documented death regardless of its disease-eligibility tag. Existing patient-attribution literal death-date rules also inspect frozen source sections, retaining exact quotations, offsets and rule versions in deaths_identity_audit.csv. Dates belonging to relatives or conditional statements are excluded. This is a separate descriptive annotation, not an extraction correction. Accepted generic record dates are never substituted. Conflicting explicit dates stay missing. In-hospital death requires an exact date within a validated inpatient interval. Undated deaths have unknown in-hospital status. No follow-up absence is interpreted as death or survival; no mortality rate or survival analysis is estimated.'),
 ('PET and CSF','Cerebral PET scope uses existing brain location or explicit cerebral/cortical wording in the retained amyloid report; body-only examinations are excluded. PET result/pattern enums were not exported in the source run. The existing deterministic PET canonicalizer is therefore applied to frozen report text as an explicit analysis-only annotation, with exact quoted evidence and rule versions in pet_identity_audit.csv. It excludes extracranial/low-dose CT findings when interpreting brain FDG uptake. Indeterminate or non-diagnostic results are not negative and are missing for positive/negative comparisons. No CAA membership or source extraction value is changed. CSF uses one patient value per canonical analyte: earliest supported admission-date measurement, or one consistent undated value. Conflicting same-date measurements are missing. Copies are not separate patients. Original laboratory-specific abnormality flags/references are retained. Analytes and units remain separate, with no universal cutoff, beta-amyloid isoform or ratio inference.'),
 ('Exploratory statistical methods','CAA-positive versus non-positive comparisons use two-sided Mann–Whitney U for continuous variables (asymptotic, tie correction and continuity correction) and two-sided Fisher exact tests for binary categories. Category-specific primary-presentation tests compare each category with all others, not a global multinomial test. P values use three decimals; values below 0.001 display <0.001. Tests are not calculated with fewer than five known patients per group, invariant values, or fewer than two pooled observations in either binary outcome category. No multiplicity adjustment or multivariable modeling is performed. P values are exploratory; non-significance does not imply equivalence.'),
 ('Limits and review status','The source review contains 887 physician-review holds. This package is conditional on those retained proposals and incomplete documentation, not a clinically adjudicated study database. Missing structured presentation components, uncertain same-day examination identity, indeterminate PET results and unspecified anticoagulant indication limit comparison. Positive-only marker ascertainment can yield 100% among a small assessed subset; available N must be read alongside each percentage. No extraction artifact or clinical database value is changed.')]


def figure_outputs(out,rows):
    captions={}
    def save(fig,stem,caption):
        fig.savefig(out/(stem+'.png'),dpi=300,bbox_inches='tight',facecolor='white')
        fig.savefig(out/(stem+'.pdf'),bbox_inches='tight',facecolor='white');plt.close(fig);captions[stem]=caption
    fig,ax=plt.subplots(figsize=(7.0,4.8),layout='constrained')
    a=[r['age_at_first_admission'] for r in rows if r['caa_positive'] and r['age_at_first_admission'] is not None]
    b=[r['age_at_first_admission'] for r in rows if not r['caa_positive'] and r['age_at_first_admission'] is not None]
    ages=a+b;bins=np.arange(5*math.floor(min(ages)/5),5*math.ceil(max(ages)/5)+6,5)
    ax.hist([a,b],bins=bins,color=[BLUE,ORANGE],label=[f'CAA-positive (N={len(a)})',f'CAA-non-positive (N={len(b)})'],alpha=.85)
    ax.set(xlabel='Age at first validated admission (years)',ylabel='Patients');ax.legend(frameon=False)
    save(fig,'fig1_age_distribution','Exact DOB and earliest validated hospital admission. No approximate ages included. Five-year age bins; each patient contributes once.')
    primary=['SPONTANEOUS_ICH','ISCHEMIC_STROKE','TIA','SEIZURE_OR_STATUS','TFNE','COGNITIVE_BEHAVIORAL','HEADACHE','OTHER_FOCAL_NEUROLOGICAL_DEFICIT','OTHER_UNKNOWN']
    labels=['Spontaneous ICH','Ischemic stroke','TIA','Seizure/status','TFNE','Cognitive/behavioral','Headache','Other focal neurological','Other/unknown']
    counts=Counter(r['primary_presentation'] for r in rows);counts['OTHER_UNKNOWN']=counts['OTHER']+counts['UNKNOWN']
    fig,ax=plt.subplots(figsize=(7.1,5.2),layout='constrained');values=[counts[k] for k in primary]
    ax.barh(labels,values,color=BLUE);ax.invert_yaxis();ax.set(xlabel='Patients',xlim=(0,max(values)*1.32+1))
    for i,n in enumerate(values):ax.text(n+.3,i,f'{n} ({100*n/len(rows):.1f}%)',va='center',fontsize=9)
    save(fig,'fig2_first_presentation','One primary category per patient (N=50). Broad focal reasons remain broad unless validated admission-linked clinical events allow refinement. Zero in a specific category is not proof that event never occurred. Other and unknown combined for display.')
    subset=[r for r in rows if r['caa_positive']]
    phenotype=[('lobar_ich','Lobar ICH'),('multiple_ich','Multiple dated ICH'),('cmb','CMB'),('lobar_cmb','Lobar CMB'),('css','cSS'),('csah','cSAH'),('cognitive_impairment','Cognitive impairment/dementia'),('any_tfne','TFNE'),('epilepsy','Chronic epilepsy'),('af','Atrial fibrillation')]
    fig,ax=plt.subplots(figsize=(7.2,5.5),layout='constrained')
    ax.barh([l for _,l in phenotype],[sum(r.get(k) is True for r in subset) for k,_ in phenotype],color=BLUE);ax.invert_yaxis();ax.set(xlabel='Patients with documented positive findings',xlim=(0,29))
    for i,(k,_) in enumerate(phenotype):
        n=sum(r.get(k) is True for r in subset);available=sum(r.get(k) is not None for r in subset)
        label=f'{n}/{available} ({100*n/available:.1f}%); known {available}/20' if available else 'NA; known 0/20'
        ax.text(n+.25,i,label,va='center',fontsize=8)
    save(fig,'fig3_caa_phenotype','CAA-positive subgroup (N=20). Bars are positive counts, labels give available-case percentages and known N/20. Unassessed markers are missing; positive-only ascertainment does not mean all 20 patients have that marker. Multiple ICH uses distinct dated events.')
    fig,axes=plt.subplots(1,2,figsize=(7.1,5.0),layout='constrained');rng=np.random.default_rng(20261023)
    for ax,family in zip(axes,['MRI','CT']):
        for x,flag,col in [(0,True,BLUE),(1,False,ORANGE)]:
            selected=[r for r in rows if r['caa_positive']==flag];exact=[r['number_of_brain_'+family] for r in selected if r['number_of_brain_'+family] is not None]
            lower=[r[family.lower()+'_exam_days_lower_bound'] for r in selected if r['number_of_brain_'+family] is None]
            ax.scatter(x+rng.uniform(-.12,.12,len(exact)),exact,color=col,s=25,alpha=.8)
            ax.scatter(x+rng.uniform(-.12,.12,len(lower)),lower,color=col,s=35,marker='^',facecolors='none')
        ax.set(xticks=[0,1],xticklabels=['CAA+','Non-positive'],ylabel=family+' examinations per patient',ylim=(-.5,None))
    axes[0].legend(handles=[Line2D([],[],marker='o',linestyle='',color=GRAY,label='Exact deduplicated count'),Line2D([],[],marker='^',linestyle='',color=GRAY,markerfacecolor='none',label='Examination-day lower bound')],loc='upper left',fontsize=8,frameon=False)
    save(fig,'fig4_imaging_counts','Each point is a patient. Circles: supported exact counts after copied-report deduplication. Open triangles: dated examination-day lower bounds where different same-day reports cannot be resolved. Exact-count Table 1 medians exclude ambiguous counts. CTA is separate.')
    fig,ax=plt.subplots(figsize=(7.2,4.8),layout='constrained');keys=['antiplatelet','therapeutic_anticoagulation','doac','vka'];names=['Antiplatelet','Therapeutic\nanticoagulation','DOAC prescribed','VKA prescribed'];x=np.arange(len(keys))
    for offset,flag,col,label in [(-.18,True,BLUE,'CAA-positive'),(.18,False,ORANGE,'CAA-non-positive')]:
        group=[r for r in rows if r['caa_positive']==flag];values=[];labels_n=[]
        for k in keys:
            v=[r['first_discharge_'+k] for r in group if r['first_discharge_'+k] is not None];n=sum(z is True for z in v)
            values.append(100*n/len(v) if v else 0);labels_n.append(f'{n}/{len(v)}' if v else 'NA')
        ax.bar(x+offset,values,width=.34,color=col,label=label)
        for xx,yy,text in zip(x+offset,values,labels_n):ax.text(xx,yy+1,text,ha='center',fontsize=8)
    ax.set(xticks=x,xticklabels=names,ylabel='Percent of patients with known exposure',ylim=(0,max(35,ax.get_ylim()[1]+10)));ax.legend(frameon=False)
    save(fig,'fig5_antithrombotics','First valid hospitalization discharge only; labels are n/available N. Missing lists are not non-use. Therapeutic indication must be explicitly encoded; prophylaxis is excluded and unencoded roles are missing. DOAC/VKA prescription does not by itself establish a therapeutic indication. Zero means no documented positive in the assessed subset.')
    combos=Counter((r['caa_positive'],r['epilepsy'],r['af']) for r in rows)
    combo_labels=[];values=[]
    for flags,n in sorted(combos.items(),key=lambda item:(-item[1],item[0])):
        combo_labels.append(' + '.join(name for name,flag in zip(['CAA','Epilepsy','AF'],flags) if flag) or 'None of the three');values.append(n)
    fig,ax=plt.subplots(figsize=(7.1,4.6),layout='constrained');ax.barh(combo_labels,values,color=BLUE);ax.invert_yaxis();ax.set(xlabel='Patients in each exclusive combination',xlim=(0,max(values)*1.3))
    for i,n in enumerate(values):ax.text(n+.2,i,f'{n} ({2*n:.1f}%)',va='center',fontsize=9)
    save(fig,'fig6_comorbidity_overlap','Mutually exclusive combinations of the frozen CAA, chronic epilepsy and AF indicators. All bars sum to 50; isolated seizure events do not establish epilepsy.')
    (out/'fig7_CSF_not_generated.txt').write_text('FIGURE_NOT_GENERATED_INSUFFICIENT_OR_NONCOMPARABLE_CSF_DATA\nOnly three patients per analyte family; unspecified beta-amyloid isoforms and laboratory comparability preclude a pooled biomarker figure. Individual values, original assays, references and flags are retained in csf_patient_measurements.csv.\n',encoding='utf-8')
    (out/'figure_captions.json').write_text(json.dumps(captions,indent=2),encoding='utf-8')
    return captions


def initialize_fonts():
    from matplotlib import font_manager
    regular=font_manager.findfont('DejaVu Sans');bold=font_manager.findfont(font_manager.FontProperties(family='DejaVu Sans',weight='bold'))
    pdfmetrics.registerFont(TTFont('CohortSans',regular));pdfmetrics.registerFont(TTFont('CohortSans-Bold',bold))
    pdfmetrics.registerFontFamily('CohortSans',normal='CohortSans',bold='CohortSans-Bold',italic='CohortSans',boldItalic='CohortSans-Bold')


class NumberedCanvas(canvas.Canvas):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.saved=[]
    def showPage(self):self.saved.append(dict(self.__dict__));self._startPage()
    def save(self):
        total=len(self.saved)
        for state in self.saved:
            self.__dict__.update(state);self.setFont('CohortSans',7)
            self.setFillColor(colors.HexColor(GRAY));self.drawString(45,24,'Exploratory descriptive analysis • frozen 50-patient cohort')
            self.drawRightString(A4[0]-45,24,f'{self._pageNumber} / {total}');canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


def pdf_outputs(out,rows,t1,t2,missing,pet,csf,captions,stamp):
    initialize_fonts();styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='BodyC',fontName='CohortSans',fontSize=9,leading=13,spaceAfter=8))
    styles.add(ParagraphStyle(name='SmallC',fontName='CohortSans',fontSize=7.4,leading=10,spaceAfter=6))
    styles.add(ParagraphStyle(name='CellC',fontName='CohortSans',fontSize=7.3,leading=10))
    styles.add(ParagraphStyle(name='TitleC',fontName='CohortSans-Bold',fontSize=16,leading=21,spaceAfter=14,textColor=colors.HexColor(BLUE)))
    styles.add(ParagraphStyle(name='HeadC',fontName='CohortSans-Bold',fontSize=11,leading=15,spaceBefore=8,spaceAfter=8,textColor=colors.HexColor(BLUE),keepWithNext=True))
    def p(text,style='BodyC'):return Paragraph(escape(str(text)),styles[style])
    def table(headers,data,widths):
        cells=[[p(h,'CellC') for h in headers]]+[[p(v,'CellC') for v in row] for row in data]
        obj=LongTable(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
        obj.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E8EFF3')),('FONTNAME',(0,0),(-1,0),'CohortSans-Bold'),
            ('GRID',(0,0),(-1,-1),.3,colors.HexColor('#BBC5CC')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return obj
    width=A4[0]-90
    def t1_flow():
        flow=[p('Table 1. Demographic and clinical characteristics of the study cohort','TitleC'),p('Overall N=50; CAA-positive n=20; CAA-non-positive n=30. Percentages use available-case denominators unless otherwise specified. Exact counts with unresolved same-day identity are missing.','SmallC')]
        for section in dict.fromkeys(r['section'] for r in t1):
            flow.append(p(section,'HeadC'));part=[r for r in t1 if r['section']==section]
            flow.append(table(['Characteristic','Overall','CAA-positive','CAA-non-positive','P value'],[[r[k] for k in ['characteristic','overall','CAA_positive','CAA_non_positive','p_value']] for r in part],[width*.32,width*.19,width*.19,width*.21,width*.09]))
        flow += [Spacer(1,10),p('Values: n/N (%) or median [IQR] (available N). Each primary presentation is exclusive; presentation components are multiple response. NA means missing or no meaningful test. Marker percentages may reflect positive-only ascertainment and must be read with N.','SmallC'),p('CAA, cerebral amyloid angiopathy; ICH, intracerebral hemorrhage; CMB, cerebral microbleeds; cSS, cortical superficial siderosis; cSAH, convexity subarachnoid hemorrhage; AF, atrial fibrillation; MRI, magnetic resonance imaging; CT, computed tomography; EEG, electroencephalography; mRS, modified Rankin Scale; IQR, interquartile range.','SmallC')]
        return flow
    def build(path,flow):SimpleDocTemplate(str(path),pagesize=A4,rightMargin=45,leftMargin=45,topMargin=43,bottomMargin=44,title=path.stem,author='Local descriptive analysis').build(flow,canvasmaker=NumberedCanvas)
    build(out/'Table1_manuscript.pdf',t1_flow())
    allages=[r['age_at_first_admission'] for r in rows];female=[r['female'] for r in rows]
    overview=[p('CAA cohort descriptive statistics','TitleC'),p('Frozen cohort • publication-style descriptive package','HeadC'),
        p(f'Source run: full_cohort_20261022T201512+0200. Analysis: {stamp}. The cohort contains 50 patients, 197 source PDFs and 1,291 source pages. Extraction results and CAA group membership are unchanged.'),
        table(['Characteristic','Value'],[['CAA-positive','20/50 (40.0%)'],['CAA-non-positive','30/50 (60.0%): includes suspected, negative and indeterminate'],['Age at first admission',continuous(allages)],['Female sex',categorical(female)],['Chronic epilepsy','9/50 (18.0%)'],['Atrial fibrillation','11/50 (22.0%)']],[width*.48,width*.52]),
        p('Interpretation and coverage','HeadC'),p('This is an exploratory analysis of the supplied physician-review artifacts, which retain 887 review holds. Unknown values are not imputed. No discharge mRS was exported. PET enums are annotated using existing literal rules with source quotations; same-day examination identities and anticoagulant indications can remain unresolved.'),
        p('Navigation','HeadC'),p('Table 1 → Figures 1–6 → Table 2 (CAA-positive) → PET → CSF → missingness → methods and definitions. Machine-readable datasets include every denominator, missingness count, raw test result, selection rule and source locator. Figure 7 is not generated because CSF sample size and assay comparability are insufficient.'),PageBreak()]
    master=overview+t1_flow()+[PageBreak()]
    titles=['Age distribution','First-admission presentation','CAA-positive phenotype','Imaging burden','First-discharge antithrombotics','CAA / epilepsy / AF overlap']
    for i,(stem,caption) in enumerate(captions.items(),1):
        master += [p(f'Figure {i}. {titles[i-1]}','TitleC')]
        image=Image(str(out/(stem+'.png')));image._restrictSize(width,490);master += [image,Spacer(1,12),p(caption),PageBreak()]
    master += [p('Table 2. CAA-positive characteristics','TitleC'),p('Restricted to the unchanged CAA-positive subgroup (n=20). Evidence streams overlap. Unknowns remain missing.','SmallC'),
        table(['Characteristic','CAA-positive n=20','Available N'],[[r['characteristic'],r['overall'],r['available_n_overall']] for r in t2],[width*.51,width*.34,width*.15]),PageBreak()]
    master += [p('PET examinations','TitleC'),p('Cerebral PET scope is documented in two patients. Body-only PET rows are retained in the audit but excluded. Existing canonical PET rules annotate frozen report text with exact source evidence, without changing source extraction or CAA membership. The amyloid PET is indeterminate under these rules; one FDG brain component has explicit nonspecific hypometabolism.'),
        table(['Measure','Overall','CAA-positive','CAA-non-positive'],[[r['characteristic'],r['overall'],r['CAA_positive'],r['CAA_non_positive']] for r in pet],[width*.4,width*.2,width*.2,width*.2]),
        p('No PET interpretation is used to redefine CAA status. FDG PET does not independently establish AD, FTD or DLB in this analysis.'),PageBreak()]
    master += [p('CSF amyloid / tau biomarkers','TitleC'),p('One selected value per patient and analyte. Original laboratory-specific abnormality flags and units are retained; no universal cutoff or inferred isoform is used. Three patients have measured CSF amyloid, total tau and p-tau.'),
        table(['Analyte / unit','Available patients','Median [IQR]','Abnormal n/N (%)'],[[r['analyte']+' / '+str(r['unit'] or 'not reported'),r['available_n'],r['summary'],r['abnormal']] for r in csf],[width*.34,width*.16,width*.27,width*.23]),
        p('Known regressions remain CSF_ABETA_UNSPECIFIED: patient 2684469520, ß-Amyloid 425 pg/ml, reference >600; patient 2826947422, ß-Amyloid 269 pg/ml, reference >600. Neither is relabeled Aβ42.'),
        p('FIGURE_NOT_GENERATED_INSUFFICIENT_OR_NONCOMPARABLE_CSF_DATA. Individual values and assay-specific references are supplied in csf_patient_measurements.csv.'),PageBreak()]
    master += [p('Missingness summary','TitleC'),p('Available N is shown as overall / CAA-positive / CAA-non-positive. Source-documentation indicators can be complete despite incomplete clinical ascertainment. Individual known negatives and missing values remain distinguishable in the patient dataset.'),
        table(['Table 1 characteristic','Available N (all / CAA / non-CAA)','Missing overall'],[[r['characteristic'],f'{r["available_n_overall"]} / {r["available_n_CAA"]} / {r["available_n_nonCAA"]}',r['missing_n_overall']] for r in missing],[width*.56,width*.28,width*.16]),PageBreak(),p('Definitions and methods','TitleC')]
    for title,text in METHODS:master += [p(title,'HeadC'),p(text)]
    master += [p('Abbreviations','HeadC'),p('CAA: cerebral amyloid angiopathy. ICH: intracerebral hemorrhage. CMB: cerebral microbleeds. cSS: cortical superficial siderosis. cSAH: convexity subarachnoid hemorrhage. AF: atrial fibrillation. MRI: magnetic resonance imaging. CT: computed tomography. CTA: CT angiography. EEG: electroencephalography. mRS: modified Rankin Scale. IQR: interquartile range. TFNE: transient focal neurological episode. DOAC: direct oral anticoagulant. VKA: vitamin K antagonist. LMWH/UFH: low-molecular-weight/unfractionated heparin. VTE: venous thromboembolism.','SmallC')]
    target=out/f'CAA_cohort_descriptive_statistics_{stamp}.pdf';build(target,master)
    return target
