// Runtime-only config: keep the frontend's TS config and source read-only.
module.exports = {
  preview: {
    host: process.env.CAR_HMI_FRONTEND_HOST || '0.0.0.0',
    port: Number(process.env.CAR_HMI_FRONTEND_PORT || 5173),
    strictPort: true,
    allowedHosts: ['localhost', 'ubuntu.orb.local'],
  },
};
