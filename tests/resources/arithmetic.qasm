OPENQASM 2.0;
include "qelib1.inc";
gate gate_MAJ q0,q1,q2 { cx q0,q1; cx q0,q2; ccx q2,q1,q0; }
gate gate_UMA q0,q1,q2 { ccx q2,q1,q0; cx q0,q2; cx q2,q1; }
gate circuit_46 q0,q1,q2,q3,q4,q5,q6,q7 { gate_MAJ q1,q4,q0; gate_MAJ q2,q5,q1; gate_MAJ q3,q6,q2; cx q3,q7; gate_UMA q3,q6,q2; gate_UMA q2,q5,q1; gate_UMA q1,q4,q0; }
gate gate_CDKMRippleCarryAdder q0,q1,q2,q3,q4,q5,q6,q7 { circuit_46 q0,q1,q2,q3,q4,q5,q6,q7; }
qreg cin[1];
qreg a[3];
qreg b[3];
qreg cout[1];
creg meas[8];
x a[0];
x b[0];
x a[1];
x b[2];
gate_CDKMRippleCarryAdder cin[0],a[0],a[1],a[2],b[0],b[1],b[2],cout[0];
barrier cin[0],a[0],a[1],a[2],b[0],b[1],b[2],cout[0];
measure cin[0] -> meas[0];
measure a[0] -> meas[1];
measure a[1] -> meas[2];
measure a[2] -> meas[3];
measure b[0] -> meas[4];
measure b[1] -> meas[5];
measure b[2] -> meas[6];
measure cout[0] -> meas[7];
