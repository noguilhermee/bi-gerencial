const { createClient } = require('@supabase/supabase-js');

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Content-Type', 'application/json');

  const checks = {
    timestamp: new Date().toISOString(),
    env: {
      SUPABASE_URL: !!process.env.SUPABASE_URL,
      SUPABASE_SERVICE_KEY: !!(process.env.SUPABASE_SERVICE_KEY || process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY),
      PG_HOST: !!process.env.PG_HOST,
      PG_USER: !!process.env.PG_USER,
      PG_PASSWORD: !!process.env.PG_PASSWORD,
      PG_DATABASE: !!process.env.PG_DATABASE,
      PG_SCHEMA: !!process.env.PG_SCHEMA
    },
    supabase: null,
    postgres: null
  };

  // Testar Supabase
  try {
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_KEY || process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY;
    if (!url || !key) throw new Error('Credenciais Supabase ausentes');
    const supabase = createClient(url, key);
    const { data, error } = await supabase.from('tab_fato_visitas').select('codigo_lr').limit(1);
    if (error) throw error;
    checks.supabase = { ok: true, rows: (data || []).length };
  } catch (err) {
    checks.supabase = { ok: false, error: err.message };
  }

  // Testar Azure Postgres
  try {
    const { Client } = require('pg');
    if (!process.env.PG_HOST) throw new Error('PG_HOST ausente');
    const client = new Client({
      host: process.env.PG_HOST,
      port: Number(process.env.PG_PORT || 5432),
      database: process.env.PG_DATABASE || 'postgres',
      user: process.env.PG_USER,
      password: process.env.PG_PASSWORD,
      ssl: { rejectUnauthorized: false },
      connectionTimeoutMillis: 5000
    });
    await client.connect();
    const schema = process.env.PG_SCHEMA || 'analytics_mart';
    const result = await client.query(`SELECT COUNT(*) as total FROM ${schema}.vw_dim_property LIMIT 1`);
    await client.end();
    checks.postgres = { ok: true, rows: result.rows[0]?.total };
  } catch (err) {
    checks.postgres = { ok: false, error: err.message };
  }

  const allOk = checks.supabase?.ok;
  return res.status(allOk ? 200 : 500).json(checks);
};
