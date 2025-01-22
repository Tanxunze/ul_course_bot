module.exports = {
  apps: [{
    name: "ul_course_bot",
    script: "./start_bot.sh",
    interpreter: "bash",
    watch: false,
    time: true,
    instance_var: 'INSTANCE_ID',
    env: {
      NODE_ENV: "production",
    },
    error_file: "./logs/err.log",
    out_file: "./logs/out.log",
    log_file: "./logs/combined.log",
    merge_logs: true,
    log_date_format: "YYYY-MM-DD HH:mm:ss Z"
  }]
}