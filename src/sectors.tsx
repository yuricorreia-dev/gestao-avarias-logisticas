import React, { memo, useEffect, useState } from 'react';
import { createRoot, Root } from 'react-dom/client';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { ArrowLeft, ArrowUpRight, ChevronDown, Download, Plus, SlidersHorizontal } from 'lucide-react';
import './sectors.css';

type Allocation = {
  id: number; quantity: number; classification: string; decision: string; status: string;
  recovery_value: number; recovery_confirmed: number; collected_value: number; collected_confirmed: number;
};
type Occurrence = {
  id: number; product: string; product_code: string; quantity: number; unit_value: number; occurrence_date: string;
  sector: string; aisle: string; reason: string; initial_classification: string; signaled: number;
  responsible: string; supplier: string; record_status: string; allocations: Allocation[];
};
type Summary = {
  gross: number; recorded_total: number; pending_value: number; repack_value: number; supplier_value: number;
  recovered: number; recovery_applied: number; collected: number; net: number;
  discount_included: number; discount_excluded: number; pending: number; monthly_limit: number; daily_limit: number;
  occurrences_count: number;
  month_realized: number; month_percent: number; today_realized: number; today_percent: number;
  daily: Array<{ date: string; gross: number; recovered: number; discounted?: number; excluded?: number }>;
  reasons: Array<{ name: string; value: number; cumulative: number }>;
  top_products: Array<{ name: string; quantity: number; value: number }>;
  signal: Record<string, number>; signal_values: Record<string, number>;
  classification_details: Record<string, { recorded: number; recovered: number; pending: number; discounted: number }>;
};
type Target = { monthly_limit: number; daily_limit: number };
type SectorPayload = { sectors: string[]; targets: Record<string, Target>; fallback: Target; period: string };
type FilterOptions = { aisle: string[]; reason: string[]; supplier: string[] };
type Props = { role: string; initialMode?: 'sectors' | 'comparison' };
type Drill = { key: string; value: string } | null;

declare global {
  interface Window {
    renderSectorsModule?: (mode: 'sectors' | 'comparison') => void;
    openOccurrenceFromSector?: (id: number, allocationId?: number) => void;
    setGlobalPeriodFromSector?: (month: string) => void;
  }
}

const currency = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const money = (value: number | undefined) => currency.format(value || 0);
const safeDate = (value: string) => new Date(`${value}T12:00:00`).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
const colors = ['#1d5574', '#24866e', '#d38b3c', '#9c5450', '#718899', '#adb9c0'];
const todayMonth = new Date().toISOString().slice(0, 7);

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin' });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Não foi possível carregar os dados.');
  return body as T;
}

function SectorApp({ role, initialMode = 'sectors' }: Props) {
  const [mode, setMode] = useState(initialMode);
  const [period, setPeriod] = useState(todayMonth);
  const [selected, setSelected] = useState('');
  const [payload, setPayload] = useState<SectorPayload | null>(null);
  const [summaries, setSummaries] = useState<Record<string, Summary>>({});
  const [occurrences, setOccurrences] = useState<Occurrence[]>([]);
  const [options, setOptions] = useState<FilterOptions>({ aisle: [], reason: [], supplier: [] });
  const [categories, setCategories] = useState<string[]>(['Avaria', 'Reembalagem', 'Fornecedor']);
  const [filters, setFilters] = useState({ from: '', to: '', classification: '', aisle: '', reason: '', supplier: '' });
  const [drill, setDrill] = useState<Drill>(null);
  const [ranking, setRanking] = useState<'net' | 'percent' | 'recovered' | 'count' | 'signal'>('percent');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [targetDialog, setTargetDialog] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');

  const monthEnd = `${period}-${String(new Date(Number(period.slice(0, 4)), Number(period.slice(5, 7)), 0).getDate()).padStart(2, '0')}`;
  const query = new URLSearchParams({ date_from: filters.from || `${period}-01`, date_to: filters.to || monthEnd, month: period });
  if (filters.classification) query.set('classification', filters.classification);
  if (filters.aisle) query.set('aisle', filters.aisle);
  if (filters.reason) query.set('reason', filters.reason);
  if (filters.supplier) query.set('supplier', filters.supplier);
  const filterString = query.toString();

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError('');
    const periodQuery = new URLSearchParams({ period });
    Promise.all([
      getJson<SectorPayload>(`/api/sectors?${periodQuery}`),
      getJson<{ aisle: string[]; reason: string[]; supplier: string[] }>('/api/options'),
      getJson<{ categories: string[] }>('/api/settings'),
    ]).then(async ([sectorPayload, optionPayload, settings]) => {
      if (cancelled) return;
      setPayload(sectorPayload); setOptions(optionPayload); setCategories(settings.categories);
      const entries = await Promise.all(sectorPayload.sectors.map(async sector => {
        const params = new URLSearchParams(filterString);
        params.set('sector', sector);
        return [sector, await getJson<Summary>(`/api/summary?${params}`)] as const;
      }));
      if (cancelled) return;
      setSummaries(Object.fromEntries(entries));
      if (selected && !sectorPayload.sectors.includes(selected)) setSelected('');
      void settings;
    }).catch(error => { if (!cancelled) setError(error.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [period, filterString]);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams(filterString);
    if (selected) params.set('sector', selected);
    getJson<{ occurrences: Occurrence[] }>(`/api/occurrences?${params}`)
      .then(data => { if (!cancelled) setOccurrences(data.occurrences); })
      .catch(error => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, [selected, filterString, mode]);

  useEffect(() => {
    window.renderSectorsModule = next => { setMode(next); setSelected(''); setDrill(null); };
    return () => { delete window.renderSectorsModule; };
  }, []);

  const metric = (sector: string) => summaries[sector];
  const target = (sector: string) => payload?.targets[sector] || payload?.fallback || { monthly_limit: 0, daily_limit: 0 };
  const targetForSelected = target(selected);
  const current = summaries[selected];
  const selectedPercent = targetForSelected.monthly_limit ? ((current?.discount_included || 0) / targetForSelected.monthly_limit) * 100 : 0;

  function updateFilter(key: keyof typeof filters, value: string) {
    setFilters(currentFilters => ({ ...currentFilters, [key]: value })); setDrill(null);
  }
  function openDrill(key: string, value: string) { setDrill({ key, value }); }
  function drillMatches(entry: Occurrence) {
    if (!drill) return true;
    if (drill.key === 'reason') return entry.reason === drill.value;
    if (drill.key === 'product') return entry.product === drill.value;
    if (drill.key === 'classification') return entry.allocations.some(item => item.classification === drill.value);
    if (drill.key === 'date') return entry.occurrence_date === drill.value;
    if (drill.key === 'signal') return String(Number(Boolean(entry.signaled))) === drill.value;
    if (drill.key === 'decision') return entry.allocations.some(item => item.decision === drill.value);
    return true;
  }
  const sourceRows = occurrences.filter(entry => !selected || entry.sector === selected).filter(drillMatches);

  async function saveTargets(form: HTMLFormElement) {
    const formData = new FormData(form);
    const sector = String(formData.get('sector') || '').trim();
    const monthly = Number(formData.get('monthly_limit'));
    const daily = Number(formData.get('daily_limit'));
    if (!sector || monthly < 0 || daily < 0) { setError('Informe setor e limites válidos.'); return; }
    setSaving(true); setError('');
    try {
      const response = await fetch('/api/targets', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ period, targets: [{ sector, monthly_limit: monthly, daily_limit: daily }] }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Não foi possível salvar as metas.');
      setTargetDialog(false); setNotice(`Metas de ${sector} salvas para ${period}.`);
      setPeriod(currentPeriod => currentPeriod);
      const fresh = await getJson<SectorPayload>(`/api/sectors?period=${period}`); setPayload(fresh);
      const summary = await getJson<Summary>(`/api/summary?sector=${encodeURIComponent(sector)}&${filterString}`);
      setSummaries(currentSummaries => ({ ...currentSummaries, [sector]: summary })); setSelected(sector);
    } catch (saveError) { setError((saveError as Error).message); }
    finally { setSaving(false); }
  }

  if (loading && !payload) return <div className="sector-loading"><span className="sector-spinner" />Carregando indicadores setoriais</div>;
  if (error && !payload) return <div className="sector-error" role="alert">{error}<button onClick={() => setPeriod(periodValue => periodValue)}>Tentar novamente</button></div>;

  const cards = payload?.sectors || [];
  const cardsHighlighted = ['Fracionado', 'Caixa', 'Organização'];
  const sorted = [...cards].sort((a, b) => {
    const one = metric(a), two = metric(b), oneTarget = target(a), twoTarget = target(b);
    const score = (data: Summary | undefined, limits: Target) => ranking === 'net' ? data?.net || 0
      : ranking === 'recovered' ? data?.recovered || 0
      : ranking === 'count' ? data?.occurrences_count || 0
      : ranking === 'signal' ? ((data?.signal?.Sinalizado || 0) / Math.max(1, (data?.signal?.Sinalizado || 0) + (data?.signal?.['Não sinalizado'] || 0)))
      : limits.monthly_limit ? (data?.discount_included || 0) / limits.monthly_limit * 100 : 0;
    return score(two, twoTarget) - score(one, oneTarget);
  });
  const scoreForSector = (sector: string) => {
    const data = metric(sector), limits = target(sector);
    if (ranking === 'net') return data?.net || 0;
    if (ranking === 'recovered') return data?.recovered || 0;
    if (ranking === 'count') return data?.occurrences_count || 0;
    if (ranking === 'signal') return (data?.signal?.Sinalizado || 0) / Math.max(1, (data?.signal?.Sinalizado || 0) + (data?.signal?.['Não sinalizado'] || 0));
    return limits.monthly_limit ? (data?.discount_included || 0) / limits.monthly_limit * 100 : 0;
  };
  const rankingMaximum = Math.max(1, ...sorted.map(scoreForSector));
  const comparisonDates = [...new Set(cardsHighlighted.flatMap(sector => (metric(sector)?.daily || []).map(day => day.date)))].sort();
  const comparisonDaily = comparisonDates.map(day => Object.fromEntries([
    ['date', day], ...cardsHighlighted.map(sector => [sector, metric(sector)?.daily.find(item => item.date === day)?.gross || 0]),
  ]));

  return <div className="sector-app">
    <header className="sector-toolbar">
      <div className="sector-title-block"><p className="sector-eyebrow">OPERAÇÃO · INDICADORES FINANCEIROS</p><h1>{mode === 'comparison' ? 'Comparativo de setores' : selected ? selected : 'Gestão por setores'}</h1><p>Metas e avarias por setor, com rastreio até cada lançamento.</p></div>
      <div className="sector-toolbar-actions">
        <label className="sector-period">Período<input type="month" value={period} onChange={event => { setPeriod(event.target.value); setFilters(value => ({ ...value, from: '', to: '' })); }} /></label>
        {role === 'admin' && <button className="sector-button subtle" onClick={() => setTargetDialog(true)}><SlidersHorizontal size={15} />Metas e limites</button>}
      </div>
    </header>
    <div className="sector-global-filters">
      <label>De<input type="date" value={filters.from} onChange={event => updateFilter('from', event.target.value)} /></label>
      <label>Até<input type="date" value={filters.to} onChange={event => updateFilter('to', event.target.value)} /></label>
      <label>Classificação<select value={filters.classification} onChange={event => updateFilter('classification', event.target.value)}><option value="">Todas</option>{categories.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Rua<select value={filters.aisle} onChange={event => updateFilter('aisle', event.target.value)}><option value="">Todas</option>{options.aisle.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Motivo<select value={filters.reason} onChange={event => updateFilter('reason', event.target.value)}><option value="">Todos</option>{options.reason.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Fornecedor<select value={filters.supplier} onChange={event => updateFilter('supplier', event.target.value)}><option value="">Todos</option>{options.supplier.map(value => <option key={value}>{value}</option>)}</select></label>
      <button className="sector-clear" onClick={() => setFilters({ from: '', to: '', classification: '', aisle: '', reason: '', supplier: '' })}>Limpar</button>
    </div>
    {notice && <div className="sector-notice" role="status">{notice}<button onClick={() => setNotice('')}>Fechar</button></div>}
    {error && <div className="sector-error" role="alert">{error}</div>}

    {mode === 'sectors' && !selected && <>
      <div className="sector-overview-heading"><div><h2>Desempenho por setor</h2><p>Limites e impacto do período selecionado.</p></div><div className="sector-rank-control"><label>Classificar por<select value={ranking} onChange={event => setRanking(event.target.value as typeof ranking)}><option value="percent">% da meta consumida</option><option value="net">Avaria líquida</option><option value="recovered">Valor recuperado</option><option value="count">Ocorrências</option><option value="signal">Taxa de sinalização</option></select></label><button className="sector-button subtle" onClick={() => setMode('comparison')}>Comparar setores <ArrowUpRight size={15} /></button></div></div>
      <div className="sector-cards">{sorted.map((sector, index) => {
        const data = metric(sector); const limits = target(sector); const discount = data?.discount_included || 0;
        const percent = limits.monthly_limit ? discount / limits.monthly_limit * 100 : 0;
        const remaining = limits.monthly_limit - discount;
        const major = cardsHighlighted.includes(sector);
        return <button className={`sector-card ${major ? 'highlighted' : ''}`} key={sector} onClick={() => { setSelected(sector); setMode('sectors'); setDrill(null); }}>
          <span className="sector-card-index">{String(index + 1).padStart(2, '0')}</span><span className="sector-card-title">{sector}</span>
          <span className="sector-card-total">{money(data?.recorded_total)}</span><span className="sector-card-caption">Total registrado · {money(data?.gross)} avaria bruta</span>
          <span className="sector-meter"><i className={percent >= 90 ? 'danger' : percent >= 70 ? 'warning' : ''} style={{ width: `${Math.min(percent, 100)}%` }} /></span>
          <span className="sector-card-foot"><span>{percent.toFixed(1)}% da meta</span><strong>{money(remaining)} disponíveis</strong></span>
          <span className="sector-card-pending">{data?.pending || 0} pendentes <ArrowUpRight size={14} /></span>
        </button>;
      })}</div>
      {role === 'admin' && <button className="sector-add" onClick={() => setTargetDialog(true)}><Plus size={16} />Adicionar setor e configurar meta</button>}
    </>}

    {mode === 'comparison' && <>
      <div className="sector-overview-heading"><div><h2>Comparação lado a lado</h2><p>O ranking usa o critério escolhido, não apenas o valor absoluto.</p></div><div className="sector-rank-control"><label>Classificar por<select value={ranking} onChange={event => setRanking(event.target.value as typeof ranking)}><option value="percent">% da meta consumida</option><option value="net">Avaria líquida</option><option value="recovered">Valor recuperado</option><option value="count">Ocorrências únicas</option><option value="signal">Taxa de sinalização</option></select></label><button className="sector-button subtle" onClick={() => setMode('sectors')}><ArrowLeft size={15} />Voltar aos setores</button></div></div>
      <div className="sector-comparison-grid">{cardsHighlighted.map((sector, index) => {
        const data = metric(sector), limits = target(sector), percent = limits.monthly_limit ? (data?.discount_included || 0) / limits.monthly_limit * 100 : 0;
        return <button key={sector} className="comparison-card" onClick={() => { setSelected(sector); setMode('sectors'); }}><span style={{ color: colors[index] }}>{sector}</span><strong>{money(data?.net)}</strong><small>Avaria líquida · {money(data?.recorded_total)} registrados</small><div className="comparison-card-row"><span>Recolhida</span><b>{money(data?.collected)}</b></div><div className="comparison-card-row"><span>Recuperada</span><b>{money(data?.recovered)}</b></div><div className="comparison-card-row"><span>Desconto aprovado</span><b>{money(data?.discount_included)}</b></div><div className="comparison-card-row"><span>Meta consumida</span><b>{percent.toFixed(1)}%</b></div><div className="comparison-card-row"><span>Ocorrências pendentes</span><b>{data?.pending || 0}</b></div><span className="comparison-meter"><i style={{ width: `${Math.min(percent, 100)}%`, background: colors[index] }} /></span></button>;
      })}</div>
      <article className="sector-chart-card comparison-ranking"><div className="sector-chart-heading"><div><h3>Ranking operacional</h3><p>Ordenado por {ranking === 'percent' ? 'percentual da meta' : ranking === 'net' ? 'avaria líquida' : ranking === 'recovered' ? 'valor recuperado' : ranking === 'count' ? 'ocorrências únicas' : 'taxa de sinalização'}.</p></div></div><div className="comparison-bars">{sorted.map((sector, index) => {const value=scoreForSector(sector);return <div className="comparison-bar" key={sector}><span>{sector}</span><i><b style={{width:`${Math.min(value/rankingMaximum*100,100)}%`,background:colors[index%colors.length]}} /></i><strong>{ranking==='count'?value.toFixed(0):ranking==='signal'?`${(value*100).toFixed(1)}%`:ranking==='percent'?`${value.toFixed(1)}%`:money(value)}</strong></div>;})}</div></article>
      <div className="sector-charts-grid">
        <ChartCard title="Evolução diária comparada" subtitle="Avaria bruta por dia nos setores destacados"><ResponsiveContainer width="100%" height={230} debounce={80}><LineChart data={comparisonDaily}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edef"/><XAxis dataKey="date" tickFormatter={safeDate} tick={{fontSize:9}}/><YAxis tickFormatter={value=>`${Math.round(value/1000)}k`} tick={{fontSize:9}}/><Tooltip labelFormatter={value=>safeDate(String(value))} formatter={value=>money(Number(value))}/><Legend/>{cardsHighlighted.map((sector,index)=><Line key={sector} type="monotone" dataKey={sector} name={sector} stroke={colors[index]} strokeWidth={2} dot={false} isAnimationActive={false} />)}</LineChart></ResponsiveContainer></ChartCard>
        <ChartCard title="Sinalização e recorrência" subtitle="Taxa, quantidade de registros e principais dados"><div className="sector-source-metrics">{cardsHighlighted.map((sector,index)=>{const data=metric(sector);const total=(data?.signal?.Sinalizado||0)+(data?.signal?.['Não sinalizado']||0);const rate=total?(data?.signal?.Sinalizado||0)/total*100:0;const topReason=data?.reasons?.[0]?.name||'—';const topProduct=data?.top_products?.[0]?.name||'—';return <div key={sector}><span style={{color:colors[index],fontWeight:700}}>{sector}</span><strong>{rate.toFixed(1)}% sinalizado · {total} ocorrências</strong><small>Motivo: {topReason} · Produto: {topProduct}</small></div>;})}</div></ChartCard>
      </div>
      <article className="sector-chart-card sector-source-table"><div className="sector-chart-heading"><div><h3>{drill?`Lançamentos · ${drill.value}`:'Lançamentos dos setores comparados'}</h3><p>{sourceRows.length} registros nos setores destacados e filtros selecionados.</p></div>{drill&&<button className="sector-clear" onClick={()=>setDrill(null)}>Limpar seleção</button>}</div><div className="sector-table-wrap"><table><thead><tr><th>Setor / data</th><th>Produto</th><th>Motivo</th><th>Valor</th><th>Sinalização</th><th></th></tr></thead><tbody>{sourceRows.filter(row=>cardsHighlighted.includes(row.sector)).slice(0,100).map(row=><tr key={row.id}><td>{row.sector}<small>{safeDate(row.occurrence_date)}</small></td><td><strong>{row.product}</strong></td><td>{row.reason}</td><td>{money(row.quantity*row.unit_value)}</td><td>{row.signaled?'Sinalizado':'Não sinalizado'}</td><td><button className="sector-open" onClick={()=>window.openOccurrenceFromSector?.(row.id,row.allocations[0]?.id)}>Abrir</button></td></tr>)}</tbody></table>{sourceRows.filter(row=>cardsHighlighted.includes(row.sector)).length===0&&<div className="sector-empty">Nenhum lançamento relacionado a este indicador.</div>}</div></article>
    </>}

    {mode === 'sectors' && selected && current && <>
      <div className="sector-detail-toolbar"><button className="sector-button subtle" onClick={() => setSelected('')}><ArrowLeft size={15} />Todos os setores</button><span className="sector-meta-source">{payload?.targets[selected] ? `Meta configurada · ${period}` : `Usando limites gerais · ${period}`}</span></div>
      <div className="sector-kpis">
        <Metric label="Limite mensal" value={money(targetForSelected.monthly_limit)} tone="navy" />
        <Metric label="Limite diário" value={money(targetForSelected.daily_limit)} tone="navy" />
        <Metric label="Avaria bruta" value={money(current.gross)} tone="red" />
        <Metric label="Valor recolhido" value={money(current.collected)} tone="green" />
        <Metric label="Avaria recuperada" value={money(current.recovered)} tone="green" />
        <Metric label="Avaria líquida" value={money(current.net)} tone="orange" />
        <Metric label="Desconto aprovado" value={money(current.discount_included)} tone="red" />
        <Metric label="Pendente de análise" value={`${current.pending}`} tone="orange" />
        <Metric label="Limite disponível" value={money(targetForSelected.monthly_limit - current.discount_included)} tone={current.discount_included > targetForSelected.monthly_limit ? 'red' : 'navy'} />
      </div>
      <div className="sector-limit-band"><div><span>Consumo do limite mensal</span><strong>{selectedPercent.toFixed(1)}%</strong></div><div className="sector-limit-track"><i className={selectedPercent>=90?'danger':selectedPercent>=70?'warning':''} style={{width:`${Math.min(selectedPercent,100)}%`}} /></div><small>{money(current.month_realized)} descontados de {money(targetForSelected.monthly_limit)} aprovados</small></div>
      <div className="sector-charts-grid">
        <ChartCard title="Evolução diária" subtitle="Avaria bruta, recuperada e meta diária"><ResponsiveContainer width="100%" height={230}><ComposedChart data={current.daily} onClick={event=>event?.activeLabel&&openDrill('date',String(event.activeLabel))}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edef"/><XAxis dataKey="date" tickFormatter={safeDate} tick={{fontSize:10}}/><YAxis tickFormatter={value=>`${Math.round(value/1000)}k`} tick={{fontSize:10}}/><Tooltip labelFormatter={value=>safeDate(String(value))} formatter={value=>money(Number(value))}/><Legend/><Area type="monotone" dataKey="gross" name="Avaria bruta" fill="#dce8ee" stroke="#315f7a"/><Line type="monotone" dataKey="recovered" name="Recuperada" stroke="#25866e" strokeWidth={2}/><Line type="monotone" dataKey="discounted" name="Desconto aprovado" stroke="#bd694c" strokeWidth={2}/><Line type="monotone" dataKey="dailyTarget" name="Meta diária" stroke="#dc9a48" strokeDasharray="6 4" data={current.daily.map(day=>({...day,dailyTarget:targetForSelected.daily_limit}))}/></ComposedChart></ResponsiveContainer></ChartCard>
        <ChartCard title="Sinalizado × não sinalizado" subtitle="Quantidade, valor e participação"><SignalChart summary={current} onDrill={value=>openDrill('signal',value)}/></ChartCard>
        <ChartCard title="Motivos e Pareto" subtitle="Valor bruto · percentual acumulado"><ResponsiveContainer width="100%" height={230}><ComposedChart data={current.reasons}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edef"/><XAxis dataKey="name" tick={{fontSize:9}} interval={0} angle={-15} textAnchor="end" height={50}/><YAxis yAxisId="left" tickFormatter={value=>`${Math.round(value/1000)}k`} tick={{fontSize:9}}/><YAxis yAxisId="right" orientation="right" domain={[0,100]} tickFormatter={value=>`${value}%`} tick={{fontSize:9}}/><Tooltip formatter={(value,name)=>name==='cumulative'?`${value}%`:money(Number(value))}/><Bar yAxisId="left" dataKey="value" name="Valor" fill="#426f86" onClick={data=>data?.name&&openDrill('reason',String(data.name))}/><Line yAxisId="right" dataKey="cumulative" name="Pareto acumulado" stroke="#d58c43" strokeWidth={2}/></ComposedChart></ResponsiveContainer></ChartCard>
        <ChartCard title="Top 10 produtos" subtitle="Valor e quantidade de unidades"><ResponsiveContainer width="100%" height={250}><BarChart data={current.top_products} layout="vertical" margin={{left:10,right:20}}><CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e9edef"/><XAxis type="number" tickFormatter={value=>`${Math.round(value/1000)}k`} tick={{fontSize:9}}/><YAxis type="category" dataKey="name" width={105} tick={{fontSize:9}}/><Tooltip formatter={(value,name)=>name==='value'?money(Number(value)):`${value} un.`}/><Bar dataKey="value" name="Valor" fill="#247b72" radius={[0,3,3,0]} onClick={data=>data?.name&&openDrill('product',String(data.name))}/></BarChart></ResponsiveContainer><div className="sector-chart-foot">Clique em uma barra para consultar os lançamentos do produto.</div></ChartCard>
        <ChartCard title="Classificação das ocorrências" subtitle="Registrado, recuperado, pendente e descontado"><ClassificationChart summary={current} onDrill={value=>openDrill('classification',value)}/></ChartCard>
        <ChartCard title="Histórico de desconto" subtitle="Consumo do limite e registros relacionados"><ResponsiveContainer width="100%" height={230}><AreaChart data={current.daily} onClick={event=>event?.activeLabel&&openDrill('date',String(event.activeLabel))}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edef"/><XAxis dataKey="date" tickFormatter={safeDate} tick={{fontSize:9}}/><YAxis tickFormatter={value=>`${Math.round(value/1000)}k`} tick={{fontSize:9}}/><Tooltip labelFormatter={value=>safeDate(String(value))} formatter={value=>money(Number(value))}/><Area type="monotone" dataKey="discounted" name="Desconto aprovado" fill="#f2ddd6" stroke="#b45b4b"/><Area type="monotone" dataKey="excluded" name="Excluído do desconto" fill="#e8edef" stroke="#8697a1"/></AreaChart></ResponsiveContainer></ChartCard>
        <ChartCard title="Origem operacional" subtitle="Reembalagem e fornecedor não são recuperação automática"><div className="sector-source-metrics"><div><span>Reembalagem realizada</span><strong>{money(current.repack_value)}</strong><small>{current.classification_details?.['Reembalagem']?.pending || 0} pendentes</small></div><div><span>Fornecedor encaminhado</span><strong>{money(current.supplier_value)}</strong><small>Valor recuperado: {money(current.classification_details?.['Fornecedor']?.recovered)}</small></div><div><span>Valor pendente de análise</span><strong>{money(current.pending_value)}</strong><small>Excluído do desconto: {money(current.discount_excluded)}</small></div></div></ChartCard>
      </div>
      <article className="sector-chart-card sector-source-table"><div className="sector-chart-heading"><div><h3>{drill ? `Lançamentos · ${drill.value}` : 'Lançamentos do setor'}</h3><p>{sourceRows.length} registros · {selected} · período ${period}</p></div>{drill && <button className="sector-clear" onClick={()=>setDrill(null)}>Limpar seleção</button>}</div><div className="sector-table-wrap"><table><thead><tr><th>Data / código</th><th>Produto</th><th>Rua / motivo</th><th>Classificação e decisão</th><th>Sinalização</th><th>Valor</th><th></th></tr></thead><tbody>{sourceRows.slice(0,100).map(row=><tr key={row.id}><td>{safeDate(row.occurrence_date)}<small>{row.product_code || `#${row.id}`}</small></td><td><strong>{row.product}</strong></td><td>{row.aisle || '—'}<small>{row.reason}</small></td><td>{row.allocations.map(item=><button className="sector-inline-link" key={item.id} onClick={()=>window.openOccurrenceFromSector?.(row.id,item.id)}>{item.classification} · {item.decision}</button>)}</td><td>{row.signaled?'Sinalizado':'Não sinalizado'}</td><td>{money(row.quantity*row.unit_value)}</td><td><button className="sector-open" onClick={()=>window.openOccurrenceFromSector?.(row.id,row.allocations[0]?.id)}>Abrir</button></td></tr>)}</tbody></table>{sourceRows.length===0&&<div className="sector-empty">Nenhum lançamento relacionado a este indicador.</div>}</div></article>
    </>}

    {targetDialog && <TargetDialog sectors={cards} selected={selected} target={target(selected || cards[0] || '')} period={period} saving={saving} onClose={()=>setTargetDialog(false)} onSubmit={saveTargets} />}
  </div>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone: string }) {
  return <div className={`sector-metric ${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}
const ChartCard = memo(function ChartCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <article className="sector-chart-card"><div className="sector-chart-heading"><div><h3>{title}</h3><p>{subtitle}</p></div><span className="chart-scope">Real</span></div>{children}</article>;
});
function SignalChart({ summary, onDrill }: { summary: Summary; onDrill: (value: string) => void }) {
  const data = [{ name: 'Sinalizado', count: summary.signal?.Sinalizado || 0, value: summary.signal_values?.Sinalizado || 0 },
    { name: 'Não sinalizado', count: summary.signal?.['Não sinalizado'] || 0, value: summary.signal_values?.['Não sinalizado'] || 0 }];
  return <div className="signal-module"><ResponsiveContainer width="48%" height={190}><PieChart><Pie data={data} dataKey="count" nameKey="name" innerRadius={49} outerRadius={76} paddingAngle={4} onClick={entry=>entry?.name&&onDrill(entry.name==='Sinalizado'?'1':'0')}><Cell fill="#168665"/><Cell fill="#b9c5cb"/></Pie><Tooltip formatter={(value,name,item)=>name==='count'?`${value} ocorrências`:money(item.payload.value)}/></PieChart></ResponsiveContainer><div className="signal-values">{data.map((item,index)=><button onClick={()=>onDrill(index===0?'1':'0')} key={item.name}><i style={{background:index===0?'#168665':'#b9c5cb'}}/><span>{item.name}<small>{money(item.value)}</small></span><strong>{item.count}</strong></button>)}</div></div>;
}
function ClassificationChart({ summary, onDrill }: { summary: Summary; onDrill: (value: string) => void }) {
  const data = Object.entries(summary.classification_details || {}).map(([name, values]) => ({ name, ...values }));
  return <ResponsiveContainer width="100%" height={230}><BarChart data={data}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e9edef"/><XAxis dataKey="name" tick={{fontSize:9}}/><YAxis tickFormatter={value=>`${Math.round(value/1000)}k`} tick={{fontSize:9}}/><Tooltip formatter={(value,name)=>money(Number(value))}/><Legend/><Bar dataKey="recorded" name="Registrado" fill="#3d6c85" onClick={row=>row?.name&&onDrill(String(row.name))}/><Bar dataKey="recovered" name="Recuperado" fill="#26846f"/><Bar dataKey="pending" name="Pendente" fill="#d49a4b"/><Bar dataKey="discounted" name="Descontado" fill="#b75b4b"/></BarChart></ResponsiveContainer>;
}
function TargetDialog({ sectors, selected, target, period, saving, onClose, onSubmit }: {
  sectors: string[]; selected: string; target: Target; period: string; saving: boolean;
  onClose: () => void; onSubmit: (form: HTMLFormElement) => void;
}) {
  const [sector, setSector] = useState(selected || sectors[0] || '');
  const [custom, setCustom] = useState(false);
  return <div className="sector-dialog-backdrop" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}><form className="sector-target-dialog" onSubmit={event=>{event.preventDefault();onSubmit(event.currentTarget);}}>
    <div className="sector-chart-heading"><div><p className="sector-eyebrow">ADMINISTRAÇÃO · {period}</p><h3>Metas e limites</h3></div><button type="button" className="sector-modal-close" onClick={onClose}>×</button></div>
    <label>Setor{custom?<input name="sector" required maxLength={100} placeholder="Nome do novo setor" autoFocus/>:<select name="sector" value={sector} onChange={event=>setSector(event.target.value)}>{sectors.map(name=><option key={name}>{name}</option>)}</select>}</label>
    <button type="button" className="sector-text-button" onClick={()=>setCustom(value=>!value)}>{custom?'Selecionar setor existente':'Cadastrar outro setor'}</button>
    <label>Limite mensal de avaria<input name="monthly_limit" type="number" min="0" step="0.01" defaultValue={target.monthly_limit} required/></label>
    <label>Meta financeira diária<input name="daily_limit" type="number" min="0" step="0.01" defaultValue={target.daily_limit} required/></label>
    <div className="sector-dialog-actions"><button type="button" className="sector-button subtle" onClick={onClose}>Cancelar</button><button type="submit" className="sector-button primary" disabled={saving}>{saving?'Salvando…':'Salvar metas'}</button></div>
  </form></div>;
}

let root: Root | null = null;
window.renderSectorsModule = (mode: 'sectors' | 'comparison') => {
  const element = document.getElementById('sector-root');
  const role = document.body.dataset.role || 'analyst';
  if (!element) return;
  if (!root) root = createRoot(element);
  root.render(<SectorApp role={role} initialMode={mode}/>);
};
